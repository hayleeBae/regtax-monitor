"""replay 실행 조립 — HISTORICAL_REPLAY_SPEC §4·§6·§9·§12, ADR-011.

앞선 다섯 모듈(`git_cmd`·`worktree`·`answer_diff`·`report`·`golden_exec`)을 스펙 §4 의
1~11 순서로 엮는 실행 계층이다. 이 파일에는 **새로운 안전장치를 만들지 않는다** —
git 은 `git_cmd`, 골든은 `golden_exec`, 저장은 `report` 가 각자 갖고 있고, 여기서
다시 구현하면 규칙이 두 곳이 된다.

## 파이프라인은 구현하지 않고 주입받는다 (ADR-011)

인덱싱·검색·초안 생성은 `ReplayPipeline` seam 으로 들어온다. runner 는 임베딩·벡터
DB·추론 백엔드를 **import 하지 않는다** — 그래야 mock 3건 검증이 집 환경에서
결정적으로 돌고(CLAUDE.md — 테스트에서 무거운 의존성 금지), 회사에서는 같은 runner 에
실제 파이프라인만 갈아 끼울 수 있다. 스펙 §6 의 index cache 도 주입되는 파이프라인의
책임이고, runner 는 키 재료(`repo_id`·`base_commit`)를 넘기는 데까지만 한다.

## 초안은 임시 worktree 안에서만 적용한다

`git apply` 는 `replay_worktree` 가 만든 스크래치 디렉토리를 `cwd` 로 고정해 실행한다.
원본 repo 에 적용하는 경로는 존재하지 않으며 초안 승인·patch 파일 출력도 하지 않는다
(CLAUDE.md — 자동 적용은 사람 승인 게이트 우회다). replay 는 **측정 도구**다.

## 한 케이스의 실패가 나머지를 끝내지 않는다 (스펙 §9)

commit 없음 / worktree 실패 / 파이프라인 실패(index·추론 백엔드 부재 포함) / answer
diff 실패 / cleanup 실패를 `failure_kind` 로 구분해 `ReplayOutcome` 에 남기고 다음
케이스로 넘어간다. 골든 타임아웃은 `golden_exec` 이 이미 `status="error"` 로 돌려주므로
예외로 올라오지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from app.evaluation.case import ExpectedReplacement, LawInput
from app.evaluation.errors import DatasetValidationError
from app.evaluation.replay.answer_diff import (
    AnswerDiff,
    AnswerDiffError,
    check_expected_replacements,
    extract_answer_diff,
    normalize_path,
)
from app.evaluation.replay.answer_diff import _contains as text_contains
from app.evaluation.replay.fixture import PrivacyMode, ReplayFixture
from app.evaluation.replay.git_cmd import GitCommandError, run_git
from app.evaluation.replay.golden_exec import run_golden
from app.evaluation.replay.loader import ReplayFixtureLoader
from app.evaluation.replay.report import (
    GeneratedReplacement,
    ReplayOutcome,
    write_report,
)
from app.evaluation.replay.worktree import (
    ReplayWorktreeError,
    WorktreeCleanupError,
    assert_clean_worktree,
    assert_commits_exist,
    replay_worktree,
    resolve_repo_path,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
"""저장소 루트 — `repository.path`(프로젝트 상대 경로) 해석의 기준점."""

REPLAY_RUNNER_VERSION = "replay-runner-v1"

DEFAULT_FIXTURES = Path("evaluation/fixtures/replay/mock_cases.yaml")
DEFAULT_RESULT_ROOT = Path("evaluation/results")
"""산출물 기본 위치 — 이미 gitignore 대상이다."""

PATCH_TEMP_PREFIX = "regtax_replay_patch_"
"""생성 diff 를 넘기기 위한 임시 파일 접두 — 아래 `_apply_in_worktree` 참조."""

PIPELINE_STUB = "stub"
PIPELINE_REAL = "real"
"""CLI `--pipeline` 값. 기본값은 없다 — 실제 파이프라인이 기본이면 이 명령 하나로
임베딩 인덱싱과 추론이 돌아 버린다(ADR-011)."""

LOCAL_LLM_BACKEND = "local"
"""이 값일 때만 초안 생성이 전부 로컬에서 끝난다 (CLAUDE.md — 코드는 외부로 나가지 않는다)."""


# ---------------------------------------------------------------------------
# 실패 구분 (스펙 §9)
# ---------------------------------------------------------------------------

FAILURE_REPO = "repo_unavailable"
FAILURE_COMMIT = "commit_not_found"
FAILURE_DIRTY = "dirty_worktree"
FAILURE_WORKTREE = "worktree_failed"
FAILURE_PIPELINE = "pipeline_failed"
FAILURE_ANSWER_DIFF = "answer_diff_failed"
FAILURE_CLEANUP = "cleanup_failed"
"""`ReplayOutcome.failure_kind` 에 담는 어휘.

자유 문장이 아니라 **고정 코드**다 — 예외 메시지를 그대로 실으면 회사 repo 경로가
리포트로 새고(ADR-010), 실패 유형별 집계도 불가능해진다. 상세는 로그로만 남긴다.
"""

_WORKTREE_FAILURE_KINDS: dict = {
    "RepoPathError": FAILURE_REPO,
    "CommitNotFoundError": FAILURE_COMMIT,
    "DirtyWorktreeError": FAILURE_DIRTY,
    "WorktreeSetupError": FAILURE_WORKTREE,
    "WorktreeCleanupError": FAILURE_CLEANUP,
}


def _worktree_failure_kind(exc: BaseException) -> str:
    """`worktree.py` 의 예외 계층을 실패 코드로 바꾼다 (스펙 §9).

    타입 이름으로 찾는 이유: `isinstance` 사슬을 쓰면 하위 예외가 늘어날 때 상위
    분기에 먼저 걸려 조용히 뭉개진다. 모르는 하위 예외는 `worktree_failed` 다.
    """
    return _WORKTREE_FAILURE_KINDS.get(type(exc).__name__, FAILURE_WORKTREE)


# ---------------------------------------------------------------------------
# 파이프라인 seam (ADR-011)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayContext:
    """파이프라인이 받는 실행 문맥.

    `repo_id` 는 **절대경로가 아니다**. 스펙 §6 의 index cache 키 재료로 쓰이는데,
    회사 repo 경로를 그대로 키에 넣으면 캐시 파일 이름·리포트를 통해 경로가 밖으로
    나간다(ADR-010). 대신 case_id 와 경로 해시로 만든 안정적 식별자를 준다 — 같은
    repo·같은 base 면 실행이 달라도 같은 값이다.

    나머지 캐시 키 성분(임베딩 모델, chunker/indexer 버전)과 운영 index 를 덮어쓰지
    않는 책임은 **주입되는 파이프라인**에 있다. runner 는 인덱싱을 하지 않는다.
    """

    case_id: str
    worktree: Path  # 과거 시점 코드가 펼쳐진 임시 디렉토리
    repo_id: str  # index cache 키 재료 (스펙 §6) — 경로가 아닌 안정적 식별자
    base_commit: str
    law: LawInput
    timeout_seconds: int


@dataclass(frozen=True)
class PipelineOutput:
    """파이프라인이 돌려주는 것.

    초안은 **unified diff 문자열 하나**다. 파일을 직접 고치게 하지 않는 이유: 무엇이
    바뀌었는지를 `git apply --check` 로 검증할 수 있어야 지표 §7 의 `git_apply` 가
    의미를 갖고, 운영 초안 생성 경로(앵커 편집 → unified diff)의 산출물과 같은
    형태이기 때문이다.
    """

    diff_text: str
    retrieved_paths: tuple  # 검색 상위 후보 경로 (순위 순)


ReplayPipeline = Callable[[ReplayContext], PipelineOutput]


# ---------------------------------------------------------------------------
# 보조 — 식별자·파일 읽기
# ---------------------------------------------------------------------------


def repo_identifier(case_id: str, repo_path: Path) -> str:
    """index cache 키에 쓸 안정적 repo 식별자 (스펙 §6).

    경로 자체가 아니라 해시를 쓴다 — 캐시 키가 로그·파일명으로 남아도 회사 경로가
    복원되지 않는다. case_id 를 앞에 붙여 사람이 어느 케이스인지 읽을 수 있게 한다.
    """
    digest = hashlib.sha256(str(Path(repo_path).resolve()).encode("utf-8")).hexdigest()
    return f"{case_id}:{digest[:16]}"


def _read_text(path: Path) -> Optional[str]:
    """worktree 안의 파일을 읽는다 — 없거나 텍스트가 아니면 None.

    임시 worktree 는 스크래치 사본이므로 여기서 읽는 것은 원본 repo 접근이 아니다.
    실패를 예외로 올리지 않는 이유는 한 파일 때문에 케이스 전체를 잃지 않기 위해서다.
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _safe_join(worktree: Path, relative: str) -> Optional[Path]:
    """worktree 하위 경로만 돌려준다 — 밖을 가리키면 None.

    fixture 경로는 로더가 이미 절대경로·`..` 를 막지만(#0017), 로더를 거치지 않고
    구성된 fixture 객체도 여기로 올 수 있어 한 번 더 확인한다.
    """
    candidate = Path(worktree) / normalize_path(relative)
    try:
        resolved = candidate.resolve()
        root = Path(worktree).resolve()
    except OSError:  # pragma: no cover - 해석 불가 경로
        return None
    if root != resolved and root not in resolved.parents:
        return None
    return candidate


# ---------------------------------------------------------------------------
# 스펙 §4-7 — 생성 diff 검증·적용 (임시 worktree 안에서만)
# ---------------------------------------------------------------------------


def _apply_in_worktree(diff_text: str, worktree: Path) -> tuple:
    """생성 diff 를 `--check` 한 뒤 통과하면 worktree 에 적용한다.

    돌려주는 값은 `(git_apply_ok, applied)` 다. `git_apply_ok` 는 **`--check` 결과**이며
    diff 가 비어 있으면 "실행하지 않음"을 뜻하는 `None` 이다(스펙 §7 의 `git apply`
    지표는 3상태다).

    diff 를 임시 파일로 넘기는 이유: `git_cmd.run_git` 은 stdin 을 다루지 않는다(모든
    호출을 인자 배열 한 형태로 고정한 결과다). 파일은 시스템 임시 디렉토리에 만들고
    `finally` 에서 지운다 — worktree 안에 두면 `status` 로 세는 "초안이 바꾼 파일"에
    섞이고, 사용자 repo 에 patch 파일을 남기는 것은 이 도구의 일이 아니다.
    """
    if not diff_text or not diff_text.strip():
        return None, False

    handle, name = tempfile.mkstemp(prefix=PATCH_TEMP_PREFIX, suffix=".diff")
    patch_path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")
        # check=False — `--check` 실패는 예외가 아니라 기록할 지표다(스펙 §7).
        checked = run_git(
            ["apply", "--check", str(patch_path)], cwd=worktree, check=False
        )
        if checked.returncode != 0:
            return False, False
        applied = run_git(["apply", str(patch_path)], cwd=worktree, check=False)
        if applied.returncode != 0:
            # `--check` 를 통과한 patch 가 적용에 실패했다 — 지표상 apply 실패다.
            logger.warning("git apply --check 통과 후 적용에 실패했습니다.")
            return False, False
        return True, True
    except GitCommandError:
        logger.warning("git apply 실행에 실패했습니다.")
        return False, False
    finally:
        patch_path.unlink(missing_ok=True)


def _changed_files(worktree: Path) -> tuple:
    """초안이 실제로 바꾼 파일 목록 — 적용 후 worktree 의 `status --porcelain -z`.

    diff 텍스트를 파싱하지 않고 git 에게 묻는 이유: 파서를 새로 쓰면 rename·새 파일·
    경로 따옴표 처리를 여기서 또 틀린다. `-z` 는 `core.quotePath` 이스케이프를 피한다
    (`answer_diff.parse_name_status` 와 같은 이유).
    """
    try:
        proc = run_git(["status", "--porcelain", "-z"], cwd=worktree, check=False)
    except GitCommandError:
        logger.warning("worktree status 조회에 실패했습니다.")
        return ()
    if proc.returncode != 0:
        return ()

    tokens = [token for token in (proc.stdout or "").split("\0") if token]
    files: list = []
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            # rename/copy 는 다음 토큰이 원본 경로다 — 신규 경로만 센다.
            index += 1
        files.append(normalize_path(path))
    return tuple(dict.fromkeys(files))


def _detect_replacements(
    worktree: Path,
    expected: Sequence[ExpectedReplacement],
    before_texts: Mapping[str, str],
) -> tuple:
    """초안이 기대 교체를 실제로 수행했는지 worktree 파일 내용으로 확인한다.

    diff 본문에서 before/after 토큰을 추출하려 하지 않는다 — 한 줄 안에서 어느
    부분문자열이 바뀌었는지는 diff 형식이 말해 주지 않아 추측이 된다. 대신 **적용 전
    내용과 적용 후 내용**을 비교한다: 적용 전에는 `before` 가 있었고, 적용 후에는
    `after` 가 있으며 `before` 가 사라졌을 때만 수행으로 센다. 파일이 그대로면
    수행이 아니다.

    비교 규칙(`exact` / `normalized_text`)은 `answer_diff` 의 것을 그대로 쓴다 —
    fixture 대조와 채점이 다른 기준을 쓰면 같은 fixture 가 두 뜻을 갖는다.
    """
    performed: list = []
    for item in expected:
        path = normalize_path(item.path)
        target = _safe_join(worktree, path)
        if target is None:
            continue
        before_text = before_texts.get(path)
        after_text = _read_text(target)
        if before_text is None or after_text is None or before_text == after_text:
            continue
        if not text_contains(before_text, item.before, item.match_mode):
            continue
        if not text_contains(after_text, item.after, item.match_mode):
            continue
        if text_contains(after_text, item.before, item.match_mode):
            continue
        performed.append(
            GeneratedReplacement(path=path, before=item.before, after=item.after)
        )
    return tuple(performed)


# ---------------------------------------------------------------------------
# 케이스 실행 (스펙 §4)
# ---------------------------------------------------------------------------


def _run_in_worktree(
    fixture: ReplayFixture,
    pipeline: ReplayPipeline,
    worktree: Path,
    repo_path: Path,
) -> dict:
    """스펙 §4 의 6~8 — 파이프라인 호출, 스크래치 적용, 골든 실행.

    파이프라인 예외를 여기서 잡는 이유: 컨텍스트 밖으로 내보내도 정리는 되지만(Step 1
    이 `finally` 로 보장한다) 그 케이스의 부분 결과와 실패 유형 구분을 잃는다. 스펙
    §9 는 "index 실패"·"추론 백엔드 unavailable"을 **구분해 계속 진행**할 것을 전제한다.
    """
    scope = fixture.scope
    context = ReplayContext(
        case_id=fixture.case_id,
        worktree=worktree,
        repo_id=repo_identifier(fixture.case_id, repo_path),
        base_commit=fixture.repository.base_commit,
        law=fixture.law,
        timeout_seconds=fixture.execution.timeout_seconds,
    )

    # 적용 전 내용 — `_detect_replacements` 의 기준선이다.
    before_texts: dict = {}
    for item in scope.expected_replacements:
        path = normalize_path(item.path)
        target = _safe_join(worktree, path)
        if target is None:
            continue
        text = _read_text(target)
        if text is not None:
            before_texts[path] = text

    try:
        output = pipeline(context)
    except Exception:  # noqa: BLE001 - 주입된 코드의 모든 실패를 케이스 단위로 가둔다
        logger.exception("replay 파이프라인이 실패했습니다 (case=%s)", fixture.case_id)
        return {"failure_kind": FAILURE_PIPELINE}

    diff_text = output.diff_text or ""
    git_apply_ok, applied = _apply_in_worktree(diff_text, worktree)

    golden = run_golden(
        fixture.execution.golden_command,
        worktree,
        fixture.execution.timeout_seconds,
    )

    return {
        "generated_diff": diff_text,
        "generated_files": _changed_files(worktree) if applied else (),
        "generated_replacements": (
            _detect_replacements(worktree, scope.expected_replacements, before_texts)
            if applied
            else ()
        ),
        "retrieved_paths": tuple(output.retrieved_paths or ()),
        "git_apply_ok": git_apply_ok,
        "golden_status": golden.status,
        "golden_output": golden.output,
    }


def run_case(
    fixture: ReplayFixture,
    pipeline: ReplayPipeline,
    project_root: Path,
) -> ReplayOutcome:
    """fixture 한 건을 스펙 §4 순서로 실행하고 채점 원자료를 돌려준다.

    1. repo 경로 해석 → 원본 dirty 확인 → base/answer commit 존재 확인
    2. `replay_worktree` 진입 (base 시점 detached worktree)
    3. 파이프라인 호출 → 4. `git apply --check` → 5. worktree 에만 apply
    6. 골든 실행 → 7. answer diff·기대 교체 대조(원본 repo 읽기 전용)
    8. `ReplayOutcome` 구성 → 9. 컨텍스트 종료 시 cleanup(Step 1 의 `finally`)

    **예외를 밖으로 던지지 않는다.** 어떤 단계가 실패해도 `failure_kind` 가 채워진
    결과를 돌려주므로 호출자는 다음 케이스를 계속 실행할 수 있다(스펙 §9).
    """
    started = time.monotonic()
    repository = fixture.repository
    base_commit = repository.base_commit
    answer_commit = repository.answer_commit

    collected: dict = {}
    failure_kind: Optional[str] = None

    def finish() -> ReplayOutcome:
        return ReplayOutcome(
            case_id=fixture.case_id,
            answer=collected.get("answer", AnswerDiff((), (), ())),
            replacement_checks=collected.get("replacement_checks", ()),
            expected_replacements=fixture.scope.expected_replacements,
            generated_files=collected.get("generated_files", ()),
            generated_replacements=collected.get("generated_replacements", ()),
            retrieved_paths=collected.get("retrieved_paths", ()),
            git_apply_ok=collected.get("git_apply_ok"),
            golden_status=collected.get("golden_status"),
            golden_output=collected.get("golden_output"),
            generated_diff=collected.get("generated_diff"),
            answer_diff_text=collected.get("answer_diff_text"),
            failure_kind=failure_kind,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    # 1) 사전 검증 — 여기서 실패하면 worktree 를 만들지 않는다.
    try:
        repo_path = resolve_repo_path(repository, Path(project_root))
        assert_clean_worktree(repo_path)
        assert_commits_exist(repo_path, (base_commit, answer_commit))
    except ReplayWorktreeError as exc:
        logger.warning("replay 사전 검증 실패 (case=%s): %s", fixture.case_id, exc)
        failure_kind = _worktree_failure_kind(exc)
        return finish()

    # 2~6) 임시 worktree 안에서만 실행한다.
    try:
        with replay_worktree(repo_path, base_commit) as worktree:
            collected.update(_run_in_worktree(fixture, pipeline, worktree, repo_path))
    except WorktreeCleanupError as exc:
        # 본문은 끝났고 정리만 실패했다 — 모은 결과는 버리지 않는다(스펙 §9 구분).
        logger.warning("replay cleanup 실패 (case=%s): %s", fixture.case_id, exc)
        failure_kind = FAILURE_CLEANUP
    except ReplayWorktreeError as exc:
        logger.warning("replay worktree 실패 (case=%s): %s", fixture.case_id, exc)
        failure_kind = _worktree_failure_kind(exc)
        return finish()

    failure_kind = failure_kind or collected.pop("failure_kind", None)

    # 7) 정답 추출 — 원본 repo 를 commit 대 commit 으로 읽기만 한다.
    try:
        collected["answer"] = extract_answer_diff(
            repo_path, base_commit, answer_commit, fixture.scope
        )
        collected["replacement_checks"] = check_expected_replacements(
            repo_path, answer_commit, fixture.scope
        )
        collected["answer_diff_text"] = _answer_diff_text(
            repo_path, base_commit, answer_commit
        )
    except AnswerDiffError as exc:
        logger.warning("answer diff 추출 실패 (case=%s): %s", fixture.case_id, exc)
        failure_kind = failure_kind or FAILURE_ANSWER_DIFF

    return finish()


def _answer_diff_text(repo_path: Path, base_commit: str, answer_commit: str) -> Optional[str]:
    """참고 지표(normalized diff similarity)용 answer diff 본문.

    저장 여부는 `report.py` 의 privacy 게이팅이 정한다 — 계산에 쓰는 것과 디스크에
    남기는 것은 별개다. 실패해도 케이스를 실패로 만들지 않는다(참고값이다).
    """
    try:
        proc = run_git(["diff", base_commit, answer_commit], cwd=repo_path, check=False)
    except GitCommandError:
        return None
    return proc.stdout if proc.returncode == 0 else None


# ---------------------------------------------------------------------------
# 전체 실행 (스펙 §8 privacy)
# ---------------------------------------------------------------------------

_PRIVACY_STRICTNESS: dict = {
    PrivacyMode.FULL: 0,
    PrivacyMode.REDACTED: 1,
    PrivacyMode.METADATA_ONLY: 2,
}


def strictest_privacy_mode(fixtures: Sequence[ReplayFixture]) -> PrivacyMode:
    """여러 fixture 의 모드 중 **가장 엄격한** 것을 고른다 (스펙 §8).

    리포트 파일은 한 벌인데 fixture 마다 모드가 다르면, 느슨한 쪽을 택하는 순간
    `metadata_only` 로 지정한 케이스의 코드가 같은 파일에 실린다. 섞였을 때 안전한
    선택은 하나뿐이다. fixture 가 없으면 가장 엄격한 기본값을 쓴다.
    """
    if not fixtures:
        return PrivacyMode.METADATA_ONLY
    return max(
        (fixture.execution.privacy_mode for fixture in fixtures),
        key=lambda mode: _PRIVACY_STRICTNESS[PrivacyMode(mode)],
    )


def run_fixtures(
    fixtures: Sequence[ReplayFixture],
    pipeline: ReplayPipeline,
    project_root: Path,
    output_dir: Path,
    privacy_mode: Optional[PrivacyMode] = None,
) -> Path:
    """fixture 전체를 실행하고 리포트를 저장한 디렉토리를 돌려준다.

    `privacy_mode` 를 주지 않으면 fixture 들의 모드 중 가장 엄격한 것을 쓴다. 저장은
    `report.write_report` 한 곳에서만 일어난다(ARCHITECTURE 레이어 규칙).
    """
    overridden = privacy_mode is not None
    mode = PrivacyMode(privacy_mode) if overridden else strictest_privacy_mode(fixtures)

    outcomes = [run_case(fixture, pipeline, project_root) for fixture in fixtures]

    return write_report(
        outcomes,
        Path(output_dir),
        mode,
        environment={
            "replay_runner_version": REPLAY_RUNNER_VERSION,
            "case_count": len(outcomes),
            # 경로·환경변수 값은 넣지 않는다 — environment.json 은 모든 모드에서 쓰인다.
            "privacy_mode_source": "override" if overridden else "fixture_strictest",
        },
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_output_dir() -> Path:
    return PROJECT_ROOT / DEFAULT_RESULT_ROOT / f"replay-{time.strftime('%Y%m%d-%H%M%S')}"


def _external_llm_allowed(backend: str, case_count: int, allowed: bool) -> bool:
    """비-local 백엔드로 replay 를 돌려도 되는지 — 명시적 opt-in 이 없으면 False.

    `--pipeline real` 의 초안 생성은 설정된 백엔드를 쓴다. `claude` 면 **대상 코드
    스니펫이 Anthropic API 로 나간다.** 운영 `apply` 경로도 같은 동작이지만 그쪽은
    사람이 한 건씩 누르는 데 비해 replay 는 케이스를 자동으로 연속 실행하므로, 한 번
    잘못 실행하면 되돌릴 수 없는 양이 나간다(CLAUDE.md CRITICAL — 코드는 외부로 나가지
    않는다). 그래서 경고를 먼저 내고 플래그가 없으면 아예 시작하지 않는다.
    """
    if backend == LOCAL_LLM_BACKEND:
        return True

    print(
        f"WARNING: LLM_BACKEND={backend} 입니다 — replay 초안 생성 시 **대상 코드 스니펫이 "
        f"외부({backend} 백엔드 API)로 전송됩니다.**",
        file=sys.stderr,
    )
    print(
        f"WARNING: 실행 대상 케이스 {case_count}건이 연속 전송됩니다 "
        "(케이스마다 검색 상위 후보 파일 내용이 프롬프트에 실립니다).",
        file=sys.stderr,
    )
    if allowed:
        return True

    print(
        "ERROR: 외부 전송을 허용하려면 --allow-external-llm 을 명시하세요. "
        f"회사 환경에서는 LLM_BACKEND={LOCAL_LLM_BACKEND} 를 권장합니다.",
        file=sys.stderr,
    )
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    """replay 실행 진입점.

    **기본 파이프라인은 없다.** `--pipeline stub` 은 집에서 조립 경로를 결정적으로
    검증하고, `--pipeline real` 은 실제 인덱싱·검색·초안 생성을 태운다. 실제
    파이프라인을 기본값으로 붙이면 이 명령 하나로 임베딩 인덱싱과 추론이 돌아 버린다
    (ADR-011).

    무거운 모듈(`real_pipeline`·`replay_draft`)은 `--pipeline real` 분기 **안에서만**
    import 한다 — 파일 상단에 두면 "runner 는 임베딩·LLM 을 import 하지 않는다"(ADR-011)가
    깨지고, 집 환경의 mock 검증이 무거운 의존성을 끌고 온다(CLAUDE.md).
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.replay.runner",
        description=(
            "과거 개정 replay 를 임시 worktree 에서 실행하고 리포트를 저장한다. "
            "--pipeline stub 은 로컬 검증용 결정적 stub, --pipeline real 은 실제 "
            "인덱싱·검색·초안 생성이다."
        ),
    )
    parser.add_argument(
        "--pipeline",
        choices=(PIPELINE_STUB, PIPELINE_REAL),
        default=None,
        help="파이프라인 선택 (기본값 없음). stub 은 --stub 과 함께 쓴다.",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=PROJECT_ROOT / DEFAULT_FIXTURES,
        help="replay fixture YAML 경로",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"리포트 출력 디렉토리 (기본: {DEFAULT_RESULT_ROOT}/replay-<시각>)",
    )
    parser.add_argument(
        "--privacy-mode",
        choices=[mode.value for mode in PrivacyMode],
        default=None,
        help="fixture 의 privacy_mode 를 덮어쓴다 (생략 시 fixture 중 가장 엄격한 모드)",
    )
    parser.add_argument(
        "--stub",
        default=None,
        help="로컬 검증용 stub 파이프라인 (perfect|partial|empty). --pipeline stub 과 함께 쓴다.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=None,
        help=(
            "replay 인덱스 캐시 루트 override (--pipeline real 전용, "
            "기본: evaluation/replay_index)"
        ),
    )
    parser.add_argument(
        "--allow-external-llm",
        action="store_true",
        help=(
            "LLM_BACKEND 가 local 이 아닐 때 외부 전송을 명시적으로 허용한다 "
            "(--pipeline real 전용). 기본은 비활성 — 없으면 실행하지 않는다."
        ),
    )
    args = parser.parse_args(argv)

    # stub 파이프라인은 CLI 전용이므로 여기서만 import 한다.
    from app.evaluation.replay.stub_pipeline import STUB_PIPELINES, build_stub_pipeline

    stub_choices = "|".join(sorted(STUB_PIPELINES))

    # `--pipeline` 없이 `--stub` 만 준 형태를 그대로 받는다 — #0018 런북·테스트가 쓰는
    # 사용법이며 여기서 깨면 기존 검증 절차가 전부 실패한다.
    mode = args.pipeline or (PIPELINE_STUB if args.stub else None)
    if mode is None:
        print(
            "ERROR: 파이프라인이 선택되지 않았습니다. 로컬 검증은 "
            f"--pipeline stub --stub {{{stub_choices}}}, 실제 실행은 --pipeline real 입니다.",
            file=sys.stderr,
        )
        return 2

    if mode == PIPELINE_STUB:
        if not args.stub:
            print(
                f"ERROR: --pipeline stub 은 --stub {{{stub_choices}}} 를 함께 요구합니다.",
                file=sys.stderr,
            )
            return 2
        if args.stub not in STUB_PIPELINES:
            print(
                f"ERROR: 알 수 없는 stub 입니다: {args.stub!r} "
                f"(사용 가능: {', '.join(sorted(STUB_PIPELINES))})",
                file=sys.stderr,
            )
            return 2
    elif args.stub:
        print(
            "NOTE: --pipeline real 에서는 --stub 이 무시됩니다.",
            file=sys.stderr,
        )

    try:
        fixtures = ReplayFixtureLoader().load_yaml(args.fixtures)
    except DatasetValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        for detail in getattr(exc, "details", None) or ():
            print(f"  - {detail}", file=sys.stderr)
        return 1

    if mode == PIPELINE_STUB:
        pipeline: ReplayPipeline = build_stub_pipeline(args.stub, fixtures)
    else:
        # 설정은 config 경유로만 읽는다(CLAUDE.md). import 를 분기 안에 두어 stub
        # 실행이 설정 로딩까지 끌고 오지 않게 한다.
        from config import settings

        if not _external_llm_allowed(
            settings.llm_backend, len(fixtures), args.allow_external_llm
        ):
            return 2

        # 생성 스택 의존은 application 계층에만 있다(ADR-012 보강) — evaluation 안에서
        # LLM 을 import 하면 #0004 계층 가드와 충돌한다.
        from app.application.replay_draft import build_replay_draft_fn
        from app.evaluation.replay.real_pipeline import build_real_pipeline

        pipeline = build_real_pipeline(
            draft_fn=build_replay_draft_fn(),
            index_root=args.index_root,
        )

    output_dir = args.output_dir or _default_output_dir()
    target = run_fixtures(
        fixtures,
        pipeline,
        PROJECT_ROOT,
        output_dir,
        privacy_mode=PrivacyMode(args.privacy_mode) if args.privacy_mode else None,
    )
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
