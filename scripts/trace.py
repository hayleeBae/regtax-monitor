#!/usr/bin/env python3
"""
Harness Observability — 실행 흔적(trace)과 실패 레코드(failure)를 기록한다.

설계 원칙 (docs/OBSERVABILITY.md):
  - append-only: 트레이스와 실패 레코드는 수정/삭제하지 않는다 (failure.status만 갱신)
  - 모든 분류값은 .harness/taxonomy.yml 에 정의된 enum만 사용한다
  - 기록 실패가 본 작업을 중단시키면 안 된다 → 모든 기록 함수는 예외를 삼킨다

사용:
  라이브러리 — execute.py가 Recorder를 통해 run/step/escalation을 자동 기록
  CLI       — 에이전트/사람이 decision·validation 이벤트와 실패를 수동 기록

CLI 예시:
  python3 scripts/trace.py event --type decision --summary "iBatis 대신 JPA 쿼리 사용" \
      --payload '{"options":["ibatis","jpa"],"chosen":"jpa","reason_code":"consistency"}'
  python3 scripts/trace.py event --type validation --summary "원본 SP 결과와 diff 0건" --status ok
  python3 scripts/trace.py failure --category spec_mismatch --summary "급여 절사 로직 불일치"
  python3 scripts/trace.py start --stage design --task "PACE 권한 모델 설계"   # 대화형 단계용
  python3 scripts/trace.py end --outcome success
  python3 scripts/trace.py current
"""

import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

HARNESS_VERSION = "1.1.0"  # 하네스 구성(스크립트/지침/검증 규칙) 변경 시 올린다
TZ = timezone(timedelta(hours=9))

VALID_EVENT_TYPES = {
    "run_start", "run_end", "step_start", "step_end", "attempt",
    "guard", "escalation", "decision", "validation", "tool_call",
    "tool_result", "human_input", "note",
}
VALID_STAGES = {"design", "implement", "test", "secscan", "release"}
VALID_OUTCOMES = {"success", "partial", "failed", "blocked", "halted", "aborted"}


def _stamp() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


class Recorder:
    """run 하나에 대한 트레이스/실패 기록기. 모든 기록 실패는 경고 후 무시한다."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.harness_dir = self.root / ".harness"
        self.traces_dir = self.harness_dir / "traces"
        self.failures_dir = self.harness_dir / "failures"
        self.current_run_file = self.harness_dir / "current_run"
        self.run_id: Optional[str] = None
        self._disabled = False

    # --- internal helpers -------------------------------------------------

    def _warn(self, msg: str):
        print(f"  [trace] WARN: {msg}", file=sys.stderr)

    def _run_dir(self) -> Path:
        return self.traces_dir / (self.run_id or "unknown")

    def _events_file(self) -> Path:
        return self._run_dir() / "events.jsonl"

    def _next_seq(self) -> int:
        f = self._events_file()
        if not f.exists():
            return 0
        with open(f, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    def _git(self, *args) -> str:
        try:
            r = subprocess.run(["git"] + list(args), cwd=self.root,
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    # --- run lifecycle -----------------------------------------------------

    def start_run(self, *, task: str, stage: str = "implement",
                  meta: Optional[dict] = None) -> Optional[str]:
        try:
            now = datetime.now(TZ)
            self.run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            self._run_dir().mkdir(parents=True, exist_ok=True)
            self.failures_dir.mkdir(parents=True, exist_ok=True)

            run = {
                "run_id": self.run_id,
                "started_at": _stamp(),
                "finished_at": None,
                "task": task,
                "stage_entry": stage,
                "harness_version": HARNESS_VERSION,
                "git": {
                    "branch": self._git("rev-parse", "--abbrev-ref", "HEAD"),
                    "base_commit": self._git("rev-parse", "--short", "HEAD"),
                },
                "outcome": None,
                "failure_refs": [],
            }
            if meta:
                run.update(meta)
            (self._run_dir() / "run.json").write_text(
                json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
            self.current_run_file.write_text(self.run_id, encoding="utf-8")
            self.log("run_start", task, stage=stage)
            return self.run_id
        except Exception as e:
            self._warn(f"start_run 실패 — 관측 기록 없이 계속 진행: {e}")
            self._disabled = True
            return None

    def resume(self) -> Optional[str]:
        """current_run 파일에서 진행 중인 run을 이어받는다 (CLI용)."""
        try:
            if self.current_run_file.exists():
                rid = self.current_run_file.read_text(encoding="utf-8").strip()
                if rid and (self.traces_dir / rid).is_dir():
                    self.run_id = rid
                    return rid
        except Exception:
            pass
        return None

    def end_run(self, outcome: str, **extra):
        if self._disabled or not self.run_id:
            return
        try:
            self.log("run_end", f"outcome={outcome}",
                     status="ok" if outcome == "success" else "fail")
            run_file = self._run_dir() / "run.json"
            run = json.loads(run_file.read_text(encoding="utf-8"))
            run["finished_at"] = _stamp()
            run["outcome"] = outcome
            run["final_commit"] = self._git("rev-parse", "--short", "HEAD")
            run.update(extra)
            run_file.write_text(json.dumps(run, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            if self.current_run_file.exists():
                self.current_run_file.unlink()
        except Exception as e:
            self._warn(f"end_run 실패: {e}")

    # --- events -------------------------------------------------------------

    def log(self, type: str, summary: str, *, stage: str = "implement",
            step: Optional[int] = None, attempt: Optional[int] = None,
            status: Optional[str] = None, payload: Optional[dict] = None,
            refs: Optional[dict] = None, actor: str = "harness") -> Optional[str]:
        if self._disabled or not self.run_id:
            return None
        try:
            seq = self._next_seq()
            event_id = f"{self.run_id}/{seq}"
            ev = {"ts": _stamp(), "event_id": event_id, "run_id": self.run_id,
                  "stage": stage, "seq": seq, "type": type, "actor": actor,
                  "summary": summary}
            if step is not None:
                ev["step"] = step
            if attempt is not None:
                ev["attempt"] = attempt
            if status is not None:
                ev["status"] = status
            if payload:
                ev["payload"] = payload
            if refs:
                ev["refs"] = refs
            with open(self._events_file(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            return event_id
        except Exception as e:
            self._warn(f"이벤트 기록 실패: {e}")
            return None

    # --- failures -----------------------------------------------------------

    def _next_failure_id(self) -> str:
        today = datetime.now(TZ).strftime("%Y%m%d")
        prefix = f"F-{today}-"
        max_n = 0
        index = self.failures_dir / "index.jsonl"
        if index.exists():
            for line in index.read_text(encoding="utf-8").splitlines():
                try:
                    fid = json.loads(line).get("failure_id", "")
                    if fid.startswith(prefix):
                        max_n = max(max_n, int(fid.rsplit("-", 1)[-1]))
                except (json.JSONDecodeError, ValueError):
                    continue
        return f"{prefix}{max_n + 1:04d}"

    def record_failure(self, *, stage: str, category: str, summary: str,
                       step: Optional[int] = None, signal: str = "",
                       trace_refs: Optional[list] = None,
                       artifact_text: str = "",
                       artifact_name: str = "detail.log") -> Optional[str]:
        """실패 레코드 초안을 생성한다. verified_cause는 사람이 채운다."""
        if self._disabled:
            return None
        try:
            self.failures_dir.mkdir(parents=True, exist_ok=True)
            fid = self._next_failure_id()
            fdir = self.failures_dir / fid
            (fdir / "artifacts").mkdir(parents=True, exist_ok=True)

            artifact_rel = None
            if artifact_text:
                artifact_rel = f"artifacts/{artifact_name}"
                (fdir / artifact_rel).write_text(artifact_text, encoding="utf-8")

            record = {
                "failure_id": fid,
                "ts": _stamp(),
                "run_id": self.run_id,
                "stage": stage,
                "step": step,
                "trace_refs": trace_refs or [],
                "symptom": {"category": category, "signal": signal[:500],
                            "artifact": artifact_rel},
                "verified_cause": {
                    "cause_code": "unknown",
                    "description": "",
                    "verified_by": "unverified",
                    "causal_chain": [],
                },
                "resolution": {"action": None, "description": "", "commit": None},
                "status": "open",
                "recurrence_of": None,
            }
            (fdir / "failure.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

            index_line = {"failure_id": fid, "ts": record["ts"],
                          "run_id": self.run_id, "stage": stage,
                          "category": category, "cause_code": "unknown",
                          "status": "open", "summary": summary[:200]}
            with open(self.failures_dir / "index.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(index_line, ensure_ascii=False) + "\n")

            self.log("escalation", f"failure 기록: {summary[:120]}",
                     stage=stage, step=step, status="fail",
                     refs={"failure_id": fid})

            # run.json에 failure 참조 연결
            if self.run_id:
                run_file = self._run_dir() / "run.json"
                if run_file.exists():
                    run = json.loads(run_file.read_text(encoding="utf-8"))
                    run.setdefault("failure_refs", []).append(fid)
                    run_file.write_text(
                        json.dumps(run, indent=2, ensure_ascii=False),
                        encoding="utf-8")
            return fid
        except Exception as e:
            self._warn(f"실패 레코드 기록 실패: {e}")
            return None


def make_recorder(root: Optional[Path] = None) -> Recorder:
    """기본 루트(스크립트 기준 리포 루트)로 Recorder를 만든다."""
    return Recorder(root or Path(__file__).resolve().parent.parent)


# --- CLI ---------------------------------------------------------------------

def _cli():
    parser = argparse.ArgumentParser(description="Harness trace/failure recorder")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ev = sub.add_parser("event", help="현재 run에 이벤트를 추가")
    p_ev.add_argument("--type", required=True, choices=sorted(VALID_EVENT_TYPES))
    p_ev.add_argument("--summary", required=True)
    p_ev.add_argument("--stage", default="implement", choices=sorted(VALID_STAGES))
    p_ev.add_argument("--step", type=int, default=None)
    p_ev.add_argument("--status", choices=["ok", "fail", "skip"], default=None)
    p_ev.add_argument("--payload", default=None, help="JSON 문자열")
    p_ev.add_argument("--actor", default="agent",
                      choices=["agent", "subagent", "human", "ci", "harness"])

    p_fail = sub.add_parser("failure", help="실패 레코드 초안 생성")
    p_fail.add_argument("--category", required=True,
                        help="symptom category (.harness/taxonomy.yml 참조)")
    p_fail.add_argument("--summary", required=True)
    p_fail.add_argument("--stage", default="implement", choices=sorted(VALID_STAGES))
    p_fail.add_argument("--step", type=int, default=None)
    p_fail.add_argument("--signal", default="")

    p_start = sub.add_parser("start", help="run 시작 (대화형 단계: /design, /secscan 등)")
    p_start.add_argument("--task", required=True)
    p_start.add_argument("--stage", default="design", choices=sorted(VALID_STAGES))

    p_end = sub.add_parser("end", help="현재 run 종료")
    p_end.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))

    sub.add_parser("current", help="현재 run_id 출력")

    args = parser.parse_args()
    rec = make_recorder()

    if args.cmd == "start":
        rid = rec.start_run(task=args.task, stage=args.stage)
        print(rid or "")
        return

    if not rec.resume():
        # 진행 중인 run이 없으면 조용히 종료 — 기록은 부가 기능이므로 실패시키지 않는다
        print("[trace] 진행 중인 run이 없습니다. "
              "(execute.py 실행 중이거나 'trace.py start' 이후에만 기록됩니다)",
              file=sys.stderr)
        return

    if args.cmd == "current":
        print(rec.run_id)
    elif args.cmd == "event":
        payload = None
        if args.payload:
            try:
                payload = json.loads(args.payload)
            except json.JSONDecodeError:
                payload = {"raw": args.payload}
        eid = rec.log(args.type, args.summary, stage=args.stage, step=args.step,
                      status=args.status, payload=payload, actor=args.actor)
        if eid:
            print(eid)
    elif args.cmd == "failure":
        fid = rec.record_failure(stage=args.stage, category=args.category,
                                 summary=args.summary, step=args.step,
                                 signal=args.signal)
        if fid:
            print(fid)
    elif args.cmd == "end":
        rec.end_run(args.outcome)


if __name__ == "__main__":
    _cli()
