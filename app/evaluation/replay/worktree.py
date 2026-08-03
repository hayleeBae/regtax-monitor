"""replay 임시 worktree 수명주기 — HISTORICAL_REPLAY_SPEC §4·§9, ADR-011.

`git_cmd.py` 가 "git 을 안전하게 한 번 부르는" 계층이라면, 이 모듈은 그 위에서
**과거 시점 코드를 임시 디렉토리에 펼쳤다가 반드시 회수하는** 실행 계층이다.
파이프라인 호출·diff 비교·리포트 저장은 여기서 하지 않는다(#0018 Step 2~5).

## 왜 컨텍스트 매니저 하나인가

스펙 §9 는 "cleanup 은 finally 에서 수행한다"를 못박는다. 정리를 호출자에게 맡기면
예외 경로에서 빠뜨린 `worktree remove` 하나가 원본 repo 의 `.git/worktrees/` 에
쌓인다 — 회사 실제 업무 저장소가 대상이라 흔적이 남는 것 자체가 사고다(ADR-011).
그래서 생성과 정리를 한 객체 안에 묶고, `finally` 밖으로 나가는 경로를 만들지 않는다.

## 원본 repo 에 하지 않는 것

`checkout`/`reset`/`clean`/`commit`/`push` 는 스펙 §4 가 금지하고 `git_cmd` 의
allowlist 가 애초에 막는다. 이 모듈이 원본에 남기는 유일한 흔적은
`worktree add` 가 만드는 `.git/worktrees/<name>` 메타데이터이며,
`remove --force` → `prune` 으로 회수한다(ADR-011).

## 경로가 오류 메시지에 새지 않게

실데이터 repo 의 절대경로는 `path_env` 환경변수로만 들어온다(ADR-010). 그 값이
예외 메시지·리포트로 흘러나가면 회사 경로가 그대로 반출되므로, 이 모듈의 오류는
**환경변수 이름과 상대경로만** 말하고 git 출력은 `_scrub()` 으로 지운 뒤 담는다
(CLAUDE.md — 코드·내부 정보 반출 금지).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from app.evaluation.replay.fixture import ReplayRepository
from app.evaluation.replay.git_cmd import (
    GitCommandError,
    rev_parse,
    run_git,
    worktree_add,
    worktree_prune,
    worktree_remove,
)

logger = logging.getLogger(__name__)

TEMP_PREFIX = "regtax_replay_"
"""임시 root 이름 접두 — 삭제 직전 가드(`_assert_temp_root`)가 이 값을 확인한다."""

WORKTREE_DIR_NAME = "work"
"""임시 root 안에 만드는 worktree 디렉토리 이름."""

_REPO_PLACEHOLDER = "<repo>"


# ---------------------------------------------------------------------------
# 실패 구분 (스펙 §9)
# ---------------------------------------------------------------------------


class ReplayWorktreeError(RuntimeError):
    """replay worktree 계층의 실패 — 아래 하위 예외로 원인을 구분한다."""


class RepoPathError(ReplayWorktreeError):
    """repo 위치를 해석하지 못했다 — path/path_env 누락, 환경변수 미설정, git repo 아님."""


class CommitNotFoundError(ReplayWorktreeError):
    """스펙 §9 "commit 없음" — base/answer revision 이 대상 repo 에 존재하지 않는다."""


class DirtyWorktreeError(ReplayWorktreeError):
    """원본 working tree 에 커밋되지 않은 변경이 있다 (스펙 §4-1·§4-4).

    replay 를 진행하지 않고 여기서 멈춘다. dirty 상태에서 worktree 를 만들면 사용자가
    작업 중인 변경과 replay 결과가 섞여 지표 해석이 불가능하고, 사고가 났을 때 원인을
    분리할 수 없다.
    """


class WorktreeSetupError(ReplayWorktreeError):
    """스펙 §9 "worktree 실패" — 임시 root 생성 또는 `worktree add` 실패."""


class WorktreeCleanupError(ReplayWorktreeError):
    """스펙 §9 "cleanup 실패" — remove/prune/임시 디렉토리 삭제가 끝나지 않았다.

    **본문이 정상 종료했을 때만** 이 예외가 나간다. 본문에서 예외가 올라오는 중이라면
    cleanup 실패는 경고로만 남기고 원래 예외를 그대로 전파한다 — 진짜 원인을 가리지
    않기 위해서다(스펙 §9 가 두 실패를 구분하는 이유).
    """


# ---------------------------------------------------------------------------
# 경로 유틸
# ---------------------------------------------------------------------------


def _scrub(text: str, *paths: Path) -> str:
    """git 출력에서 절대경로를 지운다 — 오류 메시지에 담기 전에 반드시 통과시킨다."""
    cleaned = (text or "").strip()
    variants: set[str] = set()
    for path in paths:
        candidate = Path(path)
        variants.add(str(candidate))
        try:
            variants.add(str(candidate.resolve()))
        except OSError:  # pragma: no cover - 해석 불가 경로
            pass
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            cleaned = cleaned.replace(variant, _REPO_PLACEHOLDER)
    return cleaned


def ensure_within(target: Path, root: Path) -> Path:
    """`target` 이 `root` **하위**임을 확인하고 resolve 된 경로를 돌려준다.

    `scripts/build_replay_repos.py::ensure_within` 과 같은 방어다 — 심볼릭 링크로
    루트 밖을 가리키는 경우까지 막기 위해 양쪽을 resolve 한 뒤 비교하고,
    `target == root` 는 거부한다.
    """
    resolved_root = Path(root).resolve()
    resolved_target = Path(target).resolve()
    if resolved_target == resolved_root or resolved_root not in resolved_target.parents:
        raise ReplayWorktreeError("임시 root 밖의 경로는 다루지 않는다.")
    return resolved_target


def _assert_temp_root(tmp_root: Path) -> Path:
    """삭제 대상이 `tempfile` 이 만든 root 인지 확인한다.

    `shutil.rmtree` 는 되돌릴 수 없으므로, 대상이 시스템 임시 디렉토리 하위이고
    이름이 `TEMP_PREFIX` 로 시작할 때만 진행한다. 호출자가 넘긴 임의 경로를 지우는
    경로를 만들지 않기 위한 가드다.
    """
    resolved = Path(tmp_root).resolve()
    system_tmp = Path(tempfile.gettempdir()).resolve()
    if system_tmp not in resolved.parents:
        raise ReplayWorktreeError("임시 디렉토리가 시스템 tmp 하위가 아니다.")
    if not resolved.name.startswith(TEMP_PREFIX):
        raise ReplayWorktreeError(
            f"임시 디렉토리 이름이 {TEMP_PREFIX!r} 로 시작하지 않는다."
        )
    return resolved


def _repo_label(repository: ReplayRepository) -> str:
    """오류 메시지에 쓸 repo 식별자 — 절대경로 대신 상대경로/환경변수 이름."""
    if repository.path:
        return f"repository.path={repository.path!r}"
    if repository.path_env:
        return f"환경변수 {repository.path_env}"
    return "repository"


# ---------------------------------------------------------------------------
# 1) repo 경로 해석
# ---------------------------------------------------------------------------


def resolve_repo_path(repository: ReplayRepository, project_root: Path) -> Path:
    """fixture 의 repo 선언을 실제 디렉토리로 해석한다 (스펙 §3·§4-1).

    `path` 는 프로젝트 상대 경로(mock), `path_env` 는 절대경로를 담은 환경변수
    이름(실데이터)이다. **환경변수를 읽는 것은 여기가 처음이다** — 로더(#0017)는
    선언 계층이라 값을 읽지 않는다(ARCHITECTURE.md 레이어 규칙).

    오류 메시지에는 해석된 절대경로를 넣지 않는다. 회사 repo 경로가 로그·리포트로
    새는 것을 막기 위해 환경변수 이름 또는 상대경로만 언급한다.
    """
    label = _repo_label(repository)

    if repository.path and repository.path_env:
        raise RepoPathError(
            "repository 에 path 와 path_env 를 동시에 지정할 수 없습니다."
        )

    if repository.path:
        relative = Path(repository.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RepoPathError(
                f"repository.path 는 프로젝트 상대 경로여야 합니다: {repository.path!r}"
            )
        candidate = Path(project_root) / relative
    elif repository.path_env:
        raw = os.environ.get(repository.path_env)
        if raw is None or not raw.strip():
            raise RepoPathError(
                f"환경변수 {repository.path_env} 이(가) 설정되어 있지 않습니다. "
                "replay 대상 repo 의 절대경로를 지정하세요."
            )
        candidate = Path(raw.strip())
    else:
        raise RepoPathError("repository 에 path 또는 path_env 중 하나가 필요합니다.")

    if not candidate.is_dir():
        raise RepoPathError(f"replay 대상 repo 디렉토리가 없습니다 ({label}).")

    _assert_git_repo(candidate, label)
    return candidate


def _assert_git_repo(repo_path: Path, label: str) -> None:
    """`rev-parse --is-inside-work-tree` 로 git repo 인지 확인한다 (스펙 §4-1).

    `--git-dir` 이 아니라 `--is-inside-work-tree` 를 쓰는 이유: `--git-dir` 은
    `git_cmd.FORBIDDEN_ARGS` 에 있다(git-level 옵션으로서 대상 repo 를 바꾸는 통로라
    막아 둔 이름이고, wrapper 는 위치를 따지지 않는다). 우회로를 만드는 대신 같은
    질문을 하는 다른 질의를 쓴다. 부수 효과로 bare repo("false")도 걸러지는데,
    working tree 가 없으면 §4-4 dirty 검사가 성립하지 않으므로 그편이 맞다.

    `check=False` 로 부르는 이유: `git_cmd` 의 실패 메시지에는 `cwd` 절대경로가
    들어가므로, 그 문자열을 그대로 올리면 경로가 새어 나간다.
    """
    try:
        proc = run_git(
            ["rev-parse", "--is-inside-work-tree"], cwd=repo_path, check=False
        )
    except GitCommandError as exc:
        raise RepoPathError(
            f"git 실행에 실패했습니다 ({label}): {_scrub(str(exc), repo_path)}"
        ) from exc
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise RepoPathError(f"git working tree 를 가진 저장소가 아닙니다 ({label}).")


# ---------------------------------------------------------------------------
# 2) 사전 검증 (스펙 §4-1·§4-4)
# ---------------------------------------------------------------------------


def assert_clean_worktree(repo_path: Path) -> None:
    """원본 working tree 가 깨끗한지 확인한다 (스펙 §4-4). 아니면 `DirtyWorktreeError`.

    dirty 한 원본에서 worktree 를 만들면 사용자가 작업 중이던 변경과 replay 결과가
    섞여 지표를 해석할 수 없고, 문제가 생겼을 때 원인을 분리할 수 없다. 그래서
    "정리하고 다시 실행하라"로 되돌린다 — 이 모듈은 원본을 정리해 주지 않는다
    (`clean`/`reset` 은 스펙 §4 가 금지한다).

    변경된 파일 이름은 메시지에 담지 않고 **개수만** 알린다. 실데이터 repo 의 경로가
    리포트로 흘러가지 않게 하기 위해서다 — 확인은 사용자가 로컬에서 `git status` 로
    한다.
    """
    try:
        proc = run_git(["status", "--porcelain"], cwd=repo_path, check=False)
    except GitCommandError as exc:
        raise ReplayWorktreeError(
            f"git status 실행에 실패했습니다: {_scrub(str(exc), repo_path)}"
        ) from exc
    if proc.returncode != 0:
        raise ReplayWorktreeError(
            f"git status 가 실패했습니다 (exit {proc.returncode}): "
            f"{_scrub(proc.stderr, repo_path)}"
        )

    entries = [line for line in proc.stdout.splitlines() if line.strip()]
    if entries:
        raise DirtyWorktreeError(
            f"원본 repo 에 커밋되지 않은 변경이 {len(entries)}건 있습니다. "
            "replay 는 깨끗한 상태에서만 실행합니다 — git status 로 확인 후 정리하세요."
        )


def assert_commit_exists(repo_path: Path, revision: str) -> str:
    """revision 이 실제 commit 인지 확인하고 확정 SHA 를 돌려준다 (스펙 §4-1).

    `<rev>^{commit}` 으로 물어보는 이유: 태그(annotated tag object)나 tree 를 가리키는
    이름도 `rev-parse` 자체는 통과하므로, commit 으로 해석되는지까지 확인한다.
    존재하지 않으면 스펙 §9 의 "commit 없음"에 해당하는 `CommitNotFoundError` 다 —
    git 실행 자체가 실패한 경우(`returncode is None`)와 구분한다.
    """
    try:
        return rev_parse(f"{revision}^{{commit}}", cwd=repo_path)
    except GitCommandError as exc:
        if exc.returncode is None:
            raise ReplayWorktreeError(
                f"git rev-parse 실행에 실패했습니다: {_scrub(str(exc), repo_path)}"
            ) from exc
        raise CommitNotFoundError(
            f"대상 repo 에서 commit 을 찾을 수 없습니다: {revision!r}"
        ) from exc


def assert_commits_exist(repo_path: Path, revisions: Sequence[str]) -> dict:
    """여러 revision 을 한 번에 검증하고 `{revision: sha}` 를 돌려준다.

    base/answer 를 같이 확인하는 호출자를 위한 얇은 반복이다 — 실행을 시작한 뒤에
    answer 가 없다는 사실을 알면 worktree 를 만들었다 지우는 일이 헛수고가 된다.
    """
    return {revision: assert_commit_exists(repo_path, revision) for revision in revisions}


# ---------------------------------------------------------------------------
# 3) 컨텍스트 매니저
# ---------------------------------------------------------------------------


@contextmanager
def replay_worktree(
    repo_path: Path,
    base_commit: str,
    *,
    keep_on_error: bool = False,
) -> Iterator[Path]:
    """`base_commit` 시점을 임시 detached worktree 로 펼치고, 끝나면 반드시 회수한다.

    절차는 스펙 §4 의 1~4·11 이다:

    1. 원본 dirty 확인 + base commit 존재 확인 (진입 전에 실패시킨다)
    2. `tempfile.mkdtemp(prefix=...)` 로 임시 root
    3. `worktree add --detach <tmp>/work <base_commit>` — 브랜치를 만들지 않고
       원본 HEAD 도 움직이지 않는다
    4. `finally` 에서 `worktree remove --force` → `worktree prune` →
       임시 root `rmtree`

    `keep_on_error=True` 는 **디버깅용 opt-in** 이다. 예외로 끝났을 때만 임시
    디렉토리를 남긴다(정상 종료면 그래도 지운다) — 기본값이 `True` 면 임시 디렉토리와
    `.git/worktrees/` 항목이 계속 쌓인다.

    cleanup 이 실패해도 본문 예외를 삼키지 않는다(스펙 §9): 본문이 예외를 올리는
    중이면 경고만 남기고 원래 예외를 전파하고, 본문이 정상 종료했을 때만
    `WorktreeCleanupError` 를 올린다.
    """
    repo = Path(repo_path)

    assert_clean_worktree(repo)
    assert_commit_exists(repo, base_commit)

    try:
        tmp_root = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    except OSError as exc:
        raise WorktreeSetupError(f"임시 디렉토리를 만들지 못했습니다: {exc}") from exc

    work_path = tmp_root / WORKTREE_DIR_NAME
    failed = False
    try:
        try:
            worktree_add(work_path, base_commit, cwd=repo)
        except GitCommandError as exc:
            raise WorktreeSetupError(
                "임시 worktree 생성에 실패했습니다 "
                f"(base={base_commit!r}): {_scrub(str(exc), repo, tmp_root)}"
            ) from exc
        yield work_path
    except BaseException:
        failed = True
        raise
    finally:
        if failed and keep_on_error:
            # 경로를 통째로 찍지 않는다 — 회사 repo 경로가 섞이지 않는 임시 root 이지만,
            # 이름만으로 찾을 수 있으므로 접두만 알린다.
            logger.warning(
                "keep_on_error=True — 임시 worktree 를 남깁니다 (%s* 하위). "
                "확인 후 수동으로 지우세요.",
                TEMP_PREFIX,
            )
        else:
            problems = _cleanup(repo, work_path, tmp_root)
            if problems:
                detail = "; ".join(problems)
                if failed:
                    # 본문 예외가 진짜 원인이다. 여기서 새 예외를 던지면 그것이 가려진다.
                    logger.warning("replay worktree cleanup 실패: %s", detail)
                else:
                    raise WorktreeCleanupError(
                        f"임시 worktree 정리에 실패했습니다: {detail}"
                    )


def _cleanup(repo: Path, work_path: Path, tmp_root: Path) -> list:
    """`worktree remove --force` → `worktree prune` → 임시 root 삭제 (ADR-011 순서).

    각 단계는 앞 단계가 실패해도 이어서 시도한다 — 하나가 막혔다고 나머지를 건너뛰면
    원본 repo 에 더 많은 흔적이 남는다. 실패는 예외로 올리지 않고 문자열 목록으로
    모아 호출자(`replay_worktree` 의 `finally`)가 처리 방식을 정하게 한다.
    """
    problems: list = []

    try:
        proc = worktree_remove(work_path, cwd=repo, force=True, check=False)
        if proc.returncode != 0 and work_path.exists():
            problems.append(
                f"worktree remove 실패 (exit {proc.returncode}): "
                f"{_scrub(proc.stderr, repo, tmp_root)}"
            )
    except GitCommandError as exc:
        problems.append(f"worktree remove 실행 실패: {_scrub(str(exc), repo, tmp_root)}")

    try:
        proc = worktree_prune(cwd=repo, check=False)
        if proc.returncode != 0:
            problems.append(
                f"worktree prune 실패 (exit {proc.returncode}): "
                f"{_scrub(proc.stderr, repo, tmp_root)}"
            )
    except GitCommandError as exc:
        problems.append(f"worktree prune 실행 실패: {_scrub(str(exc), repo, tmp_root)}")

    try:
        target = _assert_temp_root(tmp_root)
    except ReplayWorktreeError as exc:
        problems.append(f"임시 디렉토리 삭제를 건너뜀: {exc}")
        return problems

    # ignore_errors=True: 지우다 만 상태여도 예외 대신 아래 존재 검사로 판정한다.
    shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        problems.append(f"임시 디렉토리를 삭제하지 못했습니다 ({TEMP_PREFIX}* 하위).")

    return problems
