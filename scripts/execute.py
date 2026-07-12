#!/usr/bin/env python3
"""
Harness Step Executor — phase 내 step을 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py <phase-dir> [--push]
                               [--max-runtime MIN] [--max-cost USD]
                               [--step-timeout SEC]

안전장치 (Safety Guards):
    1. 실행 예산   : --max-runtime(기본 240분) / --max-cost(기본 무제한, USD)
                     초과 시 즉시 중단하고 ESCALATION.md 작성
    2. 타임아웃    : Claude 1회 호출이 --step-timeout(기본 1800초)을 넘으면
                     크래시 없이 '실패한 시도'로 처리 후 재시도
    3. 안티게이밍  : step 문서(step*.md)·검증 스크립트 등 보호 파일을
                     에이전트가 수정하면 원복하고 해당 시도를 실패 처리
    4. 에스컬레이션: error / blocked / 예산 초과 시 phase 디렉토리에
                     ESCALATION.md 리포트를 남겨 사람이 이어받을 수 있게 함

Exit codes: 0 완료 / 1 step 실패 / 2 blocked / 3 예산 초과
"""

import argparse
import contextlib
import fnmatch
import importlib.util
import json
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def _load_trace_module():
    """scripts/trace.py를 로드한다 (stdlib 'trace'와의 이름 충돌 회피).
    실패해도 executor 동작에는 영향 없음 — 관측 기록은 부가 기능이다."""
    try:
        spec = importlib.util.spec_from_file_location(
            "harness_trace", Path(__file__).resolve().parent / "trace.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


_trace_mod = _load_trace_module()


@contextlib.contextmanager
def progress_indicator(label: str):
    """터미널 진행 표시기. with 문으로 사용하며 .elapsed 로 경과 시간을 읽는다."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """Phase 디렉토리 안의 step들을 순차 실행하는 하네스."""

    MAX_RETRIES = 3
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} output"
    TZ = timezone(timedelta(hours=9))

    # 안전장치 기본값
    DEFAULT_MAX_RUNTIME_MIN = 240      # phase 전체 실행 상한 (분)
    DEFAULT_STEP_TIMEOUT_SEC = 1800    # Claude 1회 호출 타임아웃 (초)

    # 에이전트가 수정하면 안 되는 파일 (안티게이밍).
    # index.json 의 "protected_paths" 배열로 프로젝트별 추가 가능.
    PROTECTED_PATTERNS = [
        "phases/*/step*.md",           # step 정의 및 AC
        "phases/*/index.json",         # 상태는 에이전트가 갱신하되 diff 대상 아님(별도 취급)
        "scripts/execute.py",
        "scripts/verify.sh",
        "scripts/test_execute.py",
        "scripts/trace.py",
        ".harness/taxonomy.yml",
        ".claude/*",
        ".claude/**/*",
        "CLAUDE.md",
    ]
    # index.json 은 에이전트가 status 갱신을 위해 수정해야 하므로 예외
    PROTECTED_EXCEPTIONS = ["phases/*/index.json"]

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False,
                 max_runtime_min: int = DEFAULT_MAX_RUNTIME_MIN,
                 max_cost_usd: float = 0.0,
                 step_timeout_sec: int = DEFAULT_STEP_TIMEOUT_SEC):
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push

        self._deadline = time.monotonic() + max_runtime_min * 60
        self._max_runtime_min = max_runtime_min
        self._max_cost = max_cost_usd
        self._step_timeout = step_timeout_sec

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])
        self._total_cost = float(idx.get("total_cost_usd", 0.0))
        self._protected = self.PROTECTED_PATTERNS + list(idx.get("protected_paths", []))

        # 관측 기록기 (.harness/traces, .harness/failures) — 실패해도 executor는 계속 동작
        self._rec = _trace_mod.Recorder(ROOT) if _trace_mod else None

    def run(self):
        self._print_header()
        self._check_blockers()
        self._checkout_branch()
        if self._rec:
            self._rec.start_run(
                task=f"{self._project} / {self._phase_name}",
                stage="implement",
                meta={"phase_dir": self._phase_dir_name,
                      "total_steps": self._total})
        guardrails = self._load_guardrails()
        self._ensure_created_at()
        self._execute_all_steps(guardrails)
        self._finalize()

    # --- 관측 기록 헬퍼 (기록 실패가 실행을 중단시키지 않도록 이중 방어) ---

    def _trace(self, type: str, summary: str, **kwargs):
        if self._rec:
            try:
                self._rec.log(type, summary, **kwargs)
            except Exception:
                pass

    def _trace_end_run(self, outcome: str):
        if self._rec:
            try:
                self._rec.end_run(outcome,
                                  total_cost_usd=round(self._total_cost, 4))
            except Exception:
                pass

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    def _checkout_branch(self):
        branch = f"feat-{self._phase_name}"

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print("  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {r.stderr.strip()}")
            print("  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _commit_step(self, step_num: int, step_name: str):
        output_rel = f"phases/{self._phase_dir_name}/step{step_num}-output.json"
        index_rel = f"phases/{self._phase_dir_name}/index.json"

        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: 코드 커밋 실패: {r.stderr.strip()}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping 커밋 실패: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at",
                          "blocked": "blocked_at", "halted": "halted_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- 안전장치: 예산 (시간/비용) ---

    def _budget_exceeded(self) -> Optional[str]:
        """예산 초과 시 사유 문자열, 아니면 None."""
        if time.monotonic() > self._deadline:
            return f"실행 시간 상한 초과 (--max-runtime {self._max_runtime_min}분)"
        if self._max_cost > 0 and self._total_cost >= self._max_cost:
            return (f"비용 상한 초과 (--max-cost ${self._max_cost:.2f}, "
                    f"누적 ${self._total_cost:.2f})")
        return None

    def _persist_cost(self):
        index = self._read_json(self._index_file)
        index["total_cost_usd"] = round(self._total_cost, 4)
        self._write_json(self._index_file, index)

    # --- 안전장치: 보호 파일 (안티게이밍) ---

    def _matches_protected(self, rel_path: str) -> bool:
        for pat in self.PROTECTED_EXCEPTIONS:
            if fnmatch.fnmatch(rel_path, pat):
                return False
        return any(fnmatch.fnmatch(rel_path, pat) for pat in self._protected)

    def _check_protected_files(self) -> list:
        """직전 커밋(HEAD) 이후 수정된 보호 파일 목록. 발견 시 원복한다."""
        modified = set()
        for args in (("diff", "--name-only", "HEAD"),
                     ("diff", "--cached", "--name-only")):
            r = self._run_git(*args)
            if r.returncode == 0:
                modified.update(f for f in r.stdout.splitlines() if f.strip())

        violations = sorted(f for f in modified if self._matches_protected(f))
        if violations:
            # 원복: 추적 중인 보호 파일을 HEAD 상태로 되돌린다
            self._run_git("checkout", "HEAD", "--", *violations)
        return violations

    # --- 안전장치: 에스컬레이션 리포트 ---

    ESCALATION_CATEGORY = {
        "budget": "timeout",        # 예산 초과
        "blocked": "env_error",     # 사용자 개입 필요 (키/인증/수동 설정)
        "error": "validation_failure",  # 재시도 소진 (AC 불통과)
    }

    def _write_escalation(self, kind: str, step: Optional[dict], detail: str):
        """무인 실행 중단 시 사람이 이어받을 수 있는 리포트를 남긴다.
        동시에 구조화된 실패 레코드(.harness/failures/)를 생성한다 —
        ESCALATION.md는 사람이 읽고, failure 레코드는 기계가 읽는다."""
        step_line = f"- Step: {step['step']} ({step['name']})" if step else "- Step: (phase 수준)"
        elapsed_min = int((time.monotonic() - (self._deadline - self._max_runtime_min * 60)) / 60)

        # 구조화 실패 레코드 (2단계 관측성) — verified_cause는 사람이 채운다
        failure_id = None
        if self._rec:
            try:
                category = next(
                    (cat for key, cat in self.ESCALATION_CATEGORY.items()
                     if key in kind),
                    "tool_error")
                if "보호 파일" in detail:
                    category = "validation_failure"
                failure_id = self._rec.record_failure(
                    stage="implement",
                    category=category,
                    summary=f"[{kind}] {self._phase_name}"
                            + (f" step {step['step']} ({step['name']})" if step else ""),
                    step=step["step"] if step else None,
                    signal=detail,
                    artifact_text=detail,
                    artifact_name="escalation-detail.md")
            except Exception:
                pass

        failure_line = (f"- 실패 레코드: `.harness/failures/{failure_id}/failure.json`"
                        if failure_id else
                        "- 실패 레코드: (기록 실패 — trace 모듈 확인 필요)")
        body = f"""# ⛔ ESCALATION — 사람 개입 필요

- 종류: **{kind}**
- Phase: {self._phase_name} ({self._phase_dir_name})
{step_line}
- 시각: {self._stamp()}
- 경과: 약 {elapsed_min}분 / 누적 비용: ${self._total_cost:.2f}
{failure_line}

## 상세

{detail}

## 다음 조치

1. 위 상세 내용과 `step*-output.json`의 stdout/stderr를 확인한다.
2. 원인 수정 후 `phases/{self._phase_dir_name}/index.json`에서 해당 step의
   status를 `"pending"`으로 되돌리고 error/blocked 필드를 제거한다.
3. 실패 레코드의 `verified_cause`를 채운다 — cause_code는 `.harness/taxonomy.yml`에
   정의된 값만 사용하고, 원인을 검증하지 못했다면 `unverified`로 남긴다.
   (이 데이터가 쌓여야 반복 실패 패턴 분석이 가능하다. 레코드를 삭제하지 말 것.)
4. 이 파일(ESCALATION.md)을 삭제하고 execute.py를 재실행한다.
"""
        (self._phase_dir / "ESCALATION.md").write_text(body, encoding="utf-8")
        print(f"  📄 ESCALATION.md 작성됨 → phases/{self._phase_dir_name}/ESCALATION.md")
        if failure_id:
            print(f"  📄 실패 레코드 → .harness/failures/{failure_id}/failure.json")

    # --- guardrails & context ---

    def _load_guardrails(self) -> str:
        sections = []
        claude_md = ROOT / "CLAUDE.md"
        if claude_md.exists():
            sections.append(f"## 프로젝트 규칙 (CLAUDE.md)\n\n{claude_md.read_text()}")
        docs_dir = ROOT / "docs"
        if docs_dir.is_dir():
            for doc in sorted(docs_dir.glob("*.md")):
                sections.append(f"## {doc.stem}\n\n{doc.read_text()}")
        return "\n\n---\n\n".join(sections) if sections else ""

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## 이전 Step 산출물\n\n" + "\n".join(lines) + "\n\n"

    def _build_preamble(self, guardrails: str, step_context: str,
                        prev_error: Optional[str] = None) -> str:
        commit_example = self.FEAT_MSG.format(
            phase=self._phase_name, num="N", name="<step-name>"
        )
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"당신은 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{guardrails}\n\n---\n\n"
            f"{step_context}{retry_section}"
            f"## 작업 규칙\n\n"
            f"1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            f"2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            f"3. 기존 테스트를 깨뜨리지 마라.\n"
            f"4. AC(Acceptance Criteria) 검증을 직접 실행하라.\n"
            f"5. 🚫 절대 금지 (위반 시 시도가 자동 무효 처리됨):\n"
            f"   - step 문서(step*.md), CLAUDE.md, scripts/의 실행·검증 스크립트,\n"
            f"     .claude/ 설정을 수정하는 행위\n"
            f"   - AC를 통과시키기 위해 기존 테스트를 삭제·skip·약화하는 행위\n"
            f"     (테스트 작성/수정이 이 step의 명시적 목표인 경우만 예외)\n"
            f"   - 검증을 우회하는 조건 분기나 하드코딩된 통과 처리\n"
            f"6. /phases/{self._phase_dir_name}/index.json의 해당 step status를 업데이트하라:\n"
            f"   - AC 통과 → \"completed\" + \"summary\" 필드에 이 step의 산출물을 한 줄로 요약\n"
            f"   - {self.MAX_RETRIES}회 수정 시도 후에도 실패 → \"error\" + \"error_message\" 기록\n"
            f"   - 사용자 개입이 필요한 경우 (API 키, 인증, 수동 설정 등) → \"blocked\" + \"blocked_reason\" 기록 후 즉시 중단\n"
            f"7. 모든 변경사항을 커밋하라:\n"
            f"   {commit_example}\n"
            f"8. 실행 흔적 기록 (관측성):\n"
            f"   - 복수 대안 중 하나를 선택하는 중요한 기술적 분기를 했다면 즉시 기록하라:\n"
            f"     python3 scripts/trace.py event --type decision --summary \"<무엇을 왜 선택했는지 한 줄>\"\n"
            f"   - AC 외의 추가 검증(원본 대비 diff, 수동 API 확인 등)을 수행했다면 기록하라:\n"
            f"     python3 scripts/trace.py event --type validation --summary \"<검증 기준>\" --status ok|fail\n"
            f"   - 에러를 만나면 먼저 유사 실패 이력을 검색하라 (과거 해결책이 탐색 시간을 줄인다):\n"
            f"     grep \"<에러 키워드>\" .harness/failures/index.jsonl\n\n---\n\n"
        )

    # --- Claude 호출 ---

    def _invoke_claude(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        prompt = preamble + step_file.read_text()
        try:
            result = subprocess.run(
                ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "json", prompt],
                cwd=self._root, capture_output=True, text=True, timeout=self._step_timeout,
            )
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as e:
            # 크래시 대신 '실패한 시도'로 처리 → 재시도 루프가 넘겨받는다
            returncode = -1
            stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = f"[TIMEOUT] Claude 호출이 {self._step_timeout}초를 초과하여 강제 종료됨"
            print(f"\n  ⏱ WARN: step timeout ({self._step_timeout}s) — 시도 실패 처리")

        if returncode not in (0, -1):
            print(f"\n  WARN: Claude가 비정상 종료됨 (code {returncode})")
            if stderr:
                print(f"  stderr: {stderr[:500]}")

        # 비용 누적 (claude -p --output-format json 의 total_cost_usd)
        try:
            payload = json.loads(stdout)
            cost = float(payload.get("total_cost_usd", 0.0))
            if cost > 0:
                self._total_cost += cost
                self._persist_cost()
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        output = {
            "step": step_num, "name": step_name,
            "exitCode": returncode,
            "stdout": stdout, "stderr": stderr,
        }
        out_path = self._phase_dir / f"step{step_num}-output.json"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        return output

    # --- 헤더 & 검증 ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print("  Harness Step Executor")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        cost_str = f"${self._max_cost:.2f}" if self._max_cost > 0 else "unlimited"
        print(f"  Budget: {self._max_runtime_min}min / {cost_str} | Step timeout: {self._step_timeout}s")
        if self._auto_push:
            print("  Auto-push: enabled")
        print(f"{'='*60}")

    def _check_blockers(self):
        if (self._phase_dir / "ESCALATION.md").exists():
            print("\n  ⛔ ESCALATION.md가 존재합니다. 내용 확인·조치 후 삭제하고 재실행하세요.")
            sys.exit(1)
        index = self._read_json(self._index_file)
        for s in reversed(index["steps"]):
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print("  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print("  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- 실행 루프 ---

    def _execute_single_step(self, step: dict, guardrails: str) -> bool:
        """단일 step 실행 (재시도 포함). 완료되면 True, 실패/차단이면 False."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            # 예산 체크 (시간/비용) — 매 시도 전
            reason = self._budget_exceeded()
            if reason:
                print(f"\n  ⛔ {reason}")
                self._trace("guard", reason, step=step_num, status="fail",
                            payload={"guard": "budget"})
                self._write_escalation("예산 초과 (budget_exceeded)", step,
                                       f"{reason}\n\n남은 step들은 실행되지 않았습니다.")
                self._update_top_index("halted")
                self._trace_end_run("halted")
                sys.exit(3)

            index = self._read_json(self._index_file)
            step_context = self._build_step_context(index)
            preamble = self._build_preamble(guardrails, step_context, prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            with progress_indicator(tag) as pi:
                self._invoke_claude(step, preamble)
                elapsed = int(pi.elapsed)

            index = self._read_json(self._index_file)
            status = next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")
            ts = self._stamp()

            # 안티게이밍: 보호 파일 수정 감지 → 원복 + 시도 무효화
            violations = self._check_protected_files()
            if violations:
                status = "protected_violation"
                err_msg = ("보호 파일 수정이 감지되어 원복함: "
                           + ", ".join(violations)
                           + " — step 문서/검증 스크립트를 고치지 말고 코드로 AC를 충족하라.")
                print(f"  🛡 Step {step_num}: 보호 파일 수정 감지 → 원복 ({', '.join(violations)})")
                self._trace("guard", f"보호 파일 수정 감지 → 원복: {', '.join(violations)}",
                            step=step_num, attempt=attempt, status="fail",
                            payload={"guard": "protected_files",
                                     "violations": violations})

            self._trace("attempt",
                        f"step {step_num} ({step_name}) attempt {attempt}: {status}",
                        step=step_num, attempt=attempt,
                        status="ok" if status == "completed" else "fail",
                        payload={"elapsed_s": elapsed, "step_status": status})

            if status == "completed":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["completed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                summary = next((s.get("summary", "") for s in index["steps"]
                                if s["step"] == step_num), "")
                self._trace("step_end", f"step {step_num} 완료: {summary or step_name}",
                            step=step_num, status="ok",
                            payload={"attempts": attempt, "elapsed_s": elapsed})
                return True

            if status == "blocked":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["blocked_at"] = ts
                self._write_json(self._index_file, index)
                reason = next((s.get("blocked_reason", "") for s in index["steps"] if s["step"] == step_num), "")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                self._trace("step_end", f"step {step_num} blocked: {reason}",
                            step=step_num, status="fail",
                            payload={"blocked_reason": reason})
                self._write_escalation("사용자 개입 필요 (blocked)", step, f"사유: {reason}")
                self._update_top_index("blocked")
                self._trace_end_run("blocked")
                sys.exit(2)

            if status != "protected_violation":
                err_msg = next(
                    (s.get("error_message", "Step did not update status") for s in index["steps"] if s["step"] == step_num),
                    "Step did not update status",
                )

            if attempt < self.MAX_RETRIES:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "pending"
                        s.pop("error_message", None)
                self._write_json(self._index_file, index)
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "error"
                        s["error_message"] = f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}"
                        s["failed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                self._trace("step_end",
                            f"step {step_num} 실패 ({self.MAX_RETRIES}회 소진)",
                            step=step_num, status="fail",
                            payload={"error": err_msg[:500]})
                self._write_escalation(f"{self.MAX_RETRIES}회 재시도 실패 (error)", step,
                                       f"마지막 에러:\n\n```\n{err_msg}\n```")
                self._update_top_index("error")
                self._trace_end_run("failed")
                sys.exit(1)

        return False  # unreachable

    def _execute_all_steps(self, guardrails: str):
        while True:
            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for s in index["steps"]:
                if s["step"] == step_num and "started_at" not in s:
                    s["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    self._trace("step_start",
                                f"step {step_num} ({pending['name']}) 시작",
                                step=step_num)
                    break

            self._execute_single_step(pending, guardrails)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")
        self._trace_end_run("success")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = f"feat-{self._phase_name}"
            r = self._run_git("push", "-u", "origin", branch)
            if r.returncode != 0:
                print(f"\n  ERROR: git push 실패: {r.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!"
              + (f" (cost ${self._total_cost:.2f})" if self._total_cost > 0 else ""))
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    parser.add_argument("--max-runtime", type=int, default=StepExecutor.DEFAULT_MAX_RUNTIME_MIN,
                        metavar="MIN", help="phase 전체 실행 시간 상한 (분, 기본 240)")
    parser.add_argument("--max-cost", type=float, default=0.0,
                        metavar="USD", help="phase 누적 API 비용 상한 (USD, 기본 0=무제한)")
    parser.add_argument("--step-timeout", type=int, default=StepExecutor.DEFAULT_STEP_TIMEOUT_SEC,
                        metavar="SEC", help="Claude 1회 호출 타임아웃 (초, 기본 1800)")
    args = parser.parse_args()

    StepExecutor(args.phase_dir, auto_push=args.push,
                 max_runtime_min=args.max_runtime,
                 max_cost_usd=args.max_cost,
                 step_timeout_sec=args.step_timeout).run()


if __name__ == "__main__":
    main()
