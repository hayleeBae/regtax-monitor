"""replay 전용 git 실행 wrapper — HISTORICAL_REPLAY_SPEC §4·§5, ADR-011.

replay 의 git 호출은 **전부 이 모듈을 통과한다**(ARCHITECTURE.md 레이어 규칙).
`fixture.py`·`loader.py` 가 선언 계층이라면 이 파일은 실행 계층의 입구다.

## 왜 wrapper 안에서 검사하나

allowlist 검사를 호출자에게 맡기면 호출 지점이 늘어날 때 한 곳만 빠뜨려도
`git checkout` 이 원본 working tree 로 들어간다. 이 프로젝트에서 원본 repo 훼손은
되돌릴 수 없는 사고이므로(CLAUDE.md — "실제 repo는 절대 수정하지 않는다"),
검사를 우회하는 공개 함수(`run_raw` 류)를 두지 않는다(ADR-011). 얇은 헬퍼들도
`subprocess` 를 직접 부르지 않고 `run_git` 을 거친다.

## `app/golden.py` 와 다른 이유

`app/golden.py` 는 `shell=True` 를 쓴다 — 운영자가 `.env` 에 직접 넣은
`GOLDEN_TEST_CMD` 에는 타당하지만, replay 의 인자는 fixture YAML 과 경로에서
흘러오므로 신뢰 수준이 다르다. 여기서는 `shell=False`(인자 배열) 고정이다
(ADR-011).

## 안전 규칙 요약

- 서브커맨드 allowlist (스펙 §5) — `checkout`/`reset`/`clean`/`commit`/`push` 는
  스펙 §4 가 명시적으로 금지한다.
- git-level 옵션(`-c`, `-C`, `--git-dir` …) 거부 — 대상 repo를 바꾸거나 임의 명령을
  실행하는 통로다.
- `shell=False` + **모든 호출에 timeout** (스펙 §5).
- 상속된 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` 제거 — 호출자 환경에 남은 값이
  worktree 안의 작업을 실제 저장소로 리다이렉트하는 것을 막는다
  (`scripts/build_replay_repos.py::_git_env` 와 같은 처리).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# allowlist (스펙 §5)
# ---------------------------------------------------------------------------

ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "rev-parse",
        "cat-file",
        "diff",
        "show",
        "worktree",
        "status",
        "apply",
    }
)
"""허용 서브커맨드 — 스펙 §5 그대로다.

읽기(`rev-parse`/`cat-file`/`diff`/`show`/`status`), 임시 worktree 생성·회수
(`worktree`), 스크래치 적용(`apply`) 뿐이다. 목록에 없는 것은 전부 거부이므로
새 git 기능이 필요하면 여기에 명시적으로 추가해야 한다(ADR-011 트레이드오프).
"""

ALLOWED_WORKTREE_ACTIONS: frozenset[str] = frozenset({"add", "remove", "prune"})
"""`worktree` 의 두 번째 토큰.

스펙 §5 는 `add`/`remove` 를 명시하고, `prune` 은 비정상 종료로 남은
`.git/worktrees/` stale 항목 회수에 필요하다(ADR-011 cleanup 절차). `list`/`lock`
등은 필요하지 않으므로 열지 않는다.
"""

FORBIDDEN_ARGS: frozenset[str] = frozenset(
    {
        "-c",
        "--exec-path",
        "--upload-pack",
        "--receive-pack",
        "-C",
        "--git-dir",
        "--work-tree",
        "--namespace",
    }
)
"""서브커맨드와 무관하게 거부하는 인자.

`-c`/`--exec-path`/`--upload-pack`/`--receive-pack` 은 설정 주입과 외부 실행파일
지정 통로이고, `-C`/`--git-dir`/`--work-tree`/`--namespace` 는 대상 repo 자체를
바꾼다 — `cwd` 로 고정한 임시 worktree 밖으로 나가는 길이다.

git-level 옵션은 서브커맨드 앞에만 놓을 수 있으므로 1차 방어는 "첫 토큰이
allowlist 서브커맨드여야 한다"는 규칙이고, 이 목록은 그 뒤를 받치는 이중 방어다.
"""

FORBIDDEN_APPLY_ARGS: frozenset[str] = frozenset(
    {"--index", "--cached", "-3", "--3way"}
)
"""`apply` 에서만 거부하는 인자 — index 를 건드려 worktree 상태를 바꾼다.

`apply --check` 와 순수 `apply` 는 허용한다(스펙 §5). `--cached` 는 `diff` 에서는
정상 옵션이므로 `apply` 로 한정해 검사한다.
"""

DEFAULT_TIMEOUT_SECONDS = 120
"""스펙 §5 — 모든 command 에 timeout. 인자를 생략하면 이 값이 쓰인다."""

_MAX_OUTPUT = 4000
"""예외 메시지에 담는 stdout/stderr 상한 (`app/golden.py::_MAX_OUTPUT` 과 같은 개념)."""

_LEAKED_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


class GitCommandError(RuntimeError):
    """git 실행 실패 — exit code, stderr 요약을 담는다.

    스펙 §9 가 실패 유형 구분을 요구하므로 호출자가 원인을 나눌 수 있도록
    `returncode`(타임아웃·실행파일 없음이면 `None`)를 노출한다.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: Optional[int] = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class GitCommandNotAllowed(GitCommandError):
    """allowlist·금지 인자 위반 — git 을 실행하기 전에 막은 경우.

    실행 실패(`GitCommandError`)와 구분한다. 이 예외는 fixture 값이 아니라 호출
    코드의 잘못을 뜻하므로 재시도 대상이 아니다.
    """


# ---------------------------------------------------------------------------
# 인자 검증
# ---------------------------------------------------------------------------


def _matches(token: str, name: str) -> bool:
    """`token` 이 옵션 `name` 인지 — 정확 일치 또는 `name=...` 형태만.

    부분일치(`in`/`startswith(name)`)를 쓰지 않는 이유: `--diff-filter=M` 이
    `--diff`… 부분일치에 걸려 정상 옵션이 막힌다.
    """
    return token == name or token.startswith(f"{name}=")


def _check_forbidden(tokens: Sequence[str], names: frozenset[str], label: str) -> None:
    for token in tokens:
        for name in names:
            if _matches(token, name):
                raise GitCommandNotAllowed(f"{label} 인자는 사용할 수 없습니다: {token}")


def validate_git_args(args: Sequence[str]) -> list[str]:
    """allowlist 검사를 통과한 인자 목록을 돌려준다. 위반 시 `GitCommandNotAllowed`.

    `run_git` 이 실행 직전에 부르는 함수다. 공개해 두는 이유는 테스트와 호출자가
    "실행 없이 허용 여부만" 확인할 수 있게 하기 위해서이고, 반대로 **검사를 건너뛰고
    실행하는 경로는 존재하지 않는다**(ADR-011).
    """
    tokens = list(args)
    if not tokens:
        raise GitCommandNotAllowed("git 인자가 비어 있습니다.")
    if any(not isinstance(token, str) for token in tokens):
        raise GitCommandNotAllowed("git 인자는 모두 문자열이어야 합니다.")

    subcommand = tokens[0]
    if subcommand not in ALLOWED_SUBCOMMANDS:
        allowed = ", ".join(sorted(ALLOWED_SUBCOMMANDS))
        raise GitCommandNotAllowed(
            f"허용되지 않은 git 서브커맨드입니다: {subcommand!r} (허용: {allowed})"
        )

    rest = tokens[1:]
    _check_forbidden(rest, FORBIDDEN_ARGS, "git-level")

    if subcommand == "worktree":
        if not rest:
            raise GitCommandNotAllowed(
                "worktree 에는 add/remove/prune 중 하나가 필요합니다."
            )
        action = rest[0]
        if action not in ALLOWED_WORKTREE_ACTIONS:
            allowed = ", ".join(sorted(ALLOWED_WORKTREE_ACTIONS))
            raise GitCommandNotAllowed(
                f"허용되지 않은 worktree 하위 명령입니다: {action!r} (허용: {allowed})"
            )

    if subcommand == "apply":
        _check_forbidden(rest, FORBIDDEN_APPLY_ARGS, "apply 의 index 변경")

    return tokens


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def _git_env() -> dict:
    """상속된 git 상태를 끊은 실행 환경.

    `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` 이 남아 있으면 `cwd` 로 지정한 임시
    worktree 에서 실행한 명령이 **실제 저장소로 리다이렉트**된다. 이 모듈은 커밋을
    만들지 않으므로 신원(user.name/email)은 주입하지 않는다.
    """
    env = os.environ.copy()
    for leaked in _LEAKED_GIT_ENV:
        env.pop(leaked, None)
    return env


def _clip(text: str) -> str:
    return (text or "").strip()[-_MAX_OUTPUT:]


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
    git_bin: str = "git",
) -> subprocess.CompletedProcess:
    """allowlist 를 통과한 git 명령 하나를 실행한다.

    - `shell=False` 고정, 모든 호출에 timeout (스펙 §5).
    - `check=True`(기본)면 비정상 종료 시 `GitCommandError`.
    - `check=False`면 `CompletedProcess` 를 그대로 돌려준다 — `apply --check` 나
      `status` 처럼 **실패 자체가 정보**인 호출을 위해서다.
    - 타임아웃·실행파일 없음·cwd 없음은 `check` 와 무관하게 `GitCommandError` 다
      (돌려줄 결과가 없다). 스펙 §9 의 실패 유형 구분용으로 메시지를 나눠 둔다.
    """
    validated = validate_git_args(args)
    if timeout is None or timeout <= 0:
        raise GitCommandError(f"timeout 은 양수여야 합니다: {timeout!r}")

    work_dir = Path(cwd)
    if not work_dir.is_dir():
        raise GitCommandError(f"git 실행 디렉토리가 없습니다: {work_dir}")

    command = [git_bin, *validated]
    shown = " ".join(validated)
    try:
        proc = subprocess.run(  # noqa: S603 - allowlist 통과 인자 배열, shell 미사용
            command,
            cwd=str(work_dir),
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitCommandError(
            f"git 실행 파일을 찾을 수 없습니다: {git_bin!r}. git 설치 여부를 확인하세요."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(
            f"git {shown} 이(가) {timeout}초 안에 끝나지 않았습니다."
        ) from exc

    if check and proc.returncode != 0:
        detail = _clip(proc.stderr or proc.stdout)
        raise GitCommandError(
            f"git {shown} 실패 (exit {proc.returncode}) — {work_dir}\n{detail}",
            returncode=proc.returncode,
            stderr=detail,
        )
    return proc


# ---------------------------------------------------------------------------
# 얇은 헬퍼 — 전부 run_git 을 거친다
# ---------------------------------------------------------------------------


def rev_parse(
    revision: str,
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    git_bin: str = "git",
) -> str:
    """revision 을 확정 SHA 로 해석한다 (스펙 §4 1번 commit 검증)."""
    proc = run_git(
        ["rev-parse", "--verify", revision],
        cwd=cwd,
        timeout=timeout,
        git_bin=git_bin,
    )
    return proc.stdout.strip()


def worktree_add(
    worktree_path: Path,
    revision: str,
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    git_bin: str = "git",
) -> subprocess.CompletedProcess:
    """`revision` 을 detached 상태로 임시 worktree 에 만든다 (스펙 §4 3번).

    `--detach` 이므로 브랜치를 만들지 않고 원본 HEAD 도 움직이지 않는다.
    """
    return run_git(
        ["worktree", "add", "--detach", str(worktree_path), revision],
        cwd=cwd,
        timeout=timeout,
        git_bin=git_bin,
    )


def worktree_remove(
    worktree_path: Path,
    *,
    cwd: Path,
    force: bool = True,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
    git_bin: str = "git",
) -> subprocess.CompletedProcess:
    """임시 worktree 를 제거한다 (스펙 §4 11번 cleanup)."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    return run_git(args, cwd=cwd, timeout=timeout, check=check, git_bin=git_bin)


def worktree_prune(
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
    git_bin: str = "git",
) -> subprocess.CompletedProcess:
    """`.git/worktrees/` 에 남은 stale 항목을 회수한다 (ADR-011 cleanup 순서)."""
    return run_git(
        ["worktree", "prune"], cwd=cwd, timeout=timeout, check=check, git_bin=git_bin
    )


def diff_name_status(
    base: str,
    head: str,
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    git_bin: str = "git",
) -> str:
    """두 revision 사이의 변경 파일 목록(status 문자 포함)을 돌려준다."""
    proc = run_git(
        ["diff", "--name-status", base, head],
        cwd=cwd,
        timeout=timeout,
        git_bin=git_bin,
    )
    return proc.stdout


def show_file(
    revision: str,
    path: str,
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    git_bin: str = "git",
) -> str:
    """특정 revision 시점의 파일 내용을 돌려준다 (`git show <rev>:<path>`)."""
    proc = run_git(
        ["show", f"{revision}:{path}"], cwd=cwd, timeout=timeout, git_bin=git_bin
    )
    return proc.stdout
