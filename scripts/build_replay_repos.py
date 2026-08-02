#!/usr/bin/env python3
"""replay mock git repo 빌더 (Issue #0017).

`evaluation/fixtures/replay_sources/<case>/{base,answer}/` 의 **평범한 파일 트리**를
읽어 `evaluation/fixtures/replay_repos/<case>/` 에 커밋 2개짜리 git repo를 만든다.

    base 트리  → commit → tag `<case>/base`
    answer 트리 → commit → tag `<case>/answer`

## 왜 `scripts/` 인가

ARCHITECTURE.md 레이어 규칙은 `app/evaluation/replay/` 를 "선언만 담는 계약 —
git 실행·worktree·파일 쓰기를 하지 않는다"로 정의한다. 빌드는 정확히 git 실행과
파일 쓰기이므로 `app/` 아래에 두면 규칙 위반이다.

## 왜 repo를 커밋하지 않고 빌드하나 (ADR-010)

생성물을 커밋하면 `.git` 중첩으로 서브모듈 취급된다. 그래서 원본 트리만 커밋하고
결과물은 gitignore 된 위치에 매번 빌드한다.

## 안전 규칙

- 삭제·쓰기 대상은 **출력 루트(`OUTPUT_ROOT`) 하위로만** 한정한다. 임의 경로를
  받아 지우는 CLI 옵션은 두지 않는다 — 오타 하나로 사용자 디렉토리가 날아간다.
- 모든 git 호출은 `shell=False`(인자 배열) + `timeout` 이다
  (HISTORICAL_REPLAY_SPEC §5).
- git 신원은 `-c user.name=` / `-c user.email=` 로 **명령마다 주입**한다.
  `git config --global` 은 건드리지 않는다.
- 태그명은 Step 1 로더의 revision 문자 규칙(`[A-Za-z0-9._/-]`)을 만족한다.

사용:
    python3 scripts/build_replay_repos.py
    python3 scripts/build_replay_repos.py --case case1_value_change
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ROOT = PROJECT_ROOT / "evaluation" / "fixtures" / "replay_sources"
"""커밋된 원본 파일 트리(읽기 전용으로만 다룬다)."""

OUTPUT_ROOT = PROJECT_ROOT / "evaluation" / "fixtures" / "replay_repos"
"""생성된 git repo 위치 — gitignore 대상이며 삭제가 허용되는 유일한 루트."""

GIT_BIN = "git"
GIT_TIMEOUT_SECONDS = 120

BASE_DIR_NAME = "base"
ANSWER_DIR_NAME = "answer"

CASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
"""케이스 디렉토리 이름 — 태그명·경로에 그대로 들어가므로 문자 집합을 제한한다."""

COMMIT_AUTHOR_NAME = "regtax-monitor replay builder"
COMMIT_AUTHOR_EMAIL = "replay-builder@example.invalid"

BASE_COMMIT_DATE = "2026-01-01T00:00:00+09:00"
ANSWER_COMMIT_DATE = "2026-01-02T00:00:00+09:00"
"""커밋 날짜를 고정해 재빌드 시 commit SHA 까지 동일하게 만든다(idempotency)."""

COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".git")


class ReplayRepoBuildError(RuntimeError):
    """빌드 실패 — CLI 는 이 예외를 0이 아닌 종료 코드로 바꾼다."""


# ---------------------------------------------------------------------------
# 경로 가드
# ---------------------------------------------------------------------------


def ensure_within(target: Path, root: Path) -> Path:
    """`target` 이 `root` **하위**임을 확인하고 resolve 된 경로를 돌려준다.

    심볼릭 링크로 루트 밖을 가리키는 경우까지 막기 위해 양쪽 모두 resolve 한 뒤
    비교한다. `target == root` 도 거부한다 — 출력 루트 자체를 지우는 일은 이
    함수의 용도가 아니다.
    """
    resolved_root = Path(root).resolve()
    resolved_target = Path(target).resolve()
    if resolved_target == resolved_root or resolved_root not in resolved_target.parents:
        raise ReplayRepoBuildError(
            f"출력 루트 밖의 경로는 다루지 않는다: {resolved_target} (루트: {resolved_root})"
        )
    return resolved_target


def reset_case_dir(case_dir: Path, output_root: Path) -> Path:
    """케이스 디렉토리를 비우고 새로 만든다. 삭제 직전에 루트 하위인지 확인한다."""
    resolved = ensure_within(case_dir, output_root)
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    return resolved


# ---------------------------------------------------------------------------
# git 실행
# ---------------------------------------------------------------------------


def _git_env(extra: Optional[dict] = None) -> dict:
    """git 호출 환경 — 신원을 고정하고 상속된 git 상태를 끊는다.

    신원을 `-c` 인자와 환경변수 양쪽에 넣는 이유: 환경변수 `GIT_AUTHOR_*` 가
    config 보다 우선하므로, 호출자 환경에 값이 남아 있으면 `-c` 만으로는 커밋
    저자가 흔들린다. 전역 설정은 어느 쪽으로도 건드리지 않는다.
    """
    env = os.environ.copy()
    for leaked in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(leaked, None)
    env["GIT_AUTHOR_NAME"] = COMMIT_AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = COMMIT_AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = COMMIT_AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = COMMIT_AUTHOR_EMAIL
    if extra:
        env.update(extra)
    return env


def _run_git(
    args: Sequence[str],
    cwd: Path,
    git_bin: str,
    env_extra: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """git 하나를 실행한다. `shell=False` + timeout 고정 (SPEC §5)."""
    command = [
        git_bin,
        "-c",
        f"user.name={COMMIT_AUTHOR_NAME}",
        "-c",
        f"user.email={COMMIT_AUTHOR_EMAIL}",
        "-c",
        "commit.gpgsign=false",
        *args,
    ]
    try:
        proc = subprocess.run(  # noqa: S603 - 인자 배열 고정, shell 미사용
            command,
            cwd=str(cwd),
            env=_git_env(env_extra),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ReplayRepoBuildError(
            f"git 실행 파일을 찾을 수 없습니다: {git_bin!r}. git 설치 여부를 확인하세요."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReplayRepoBuildError(
            f"git {' '.join(args)} 이(가) {GIT_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ReplayRepoBuildError(
            f"git {' '.join(args)} 실패 (exit {proc.returncode}) — {cwd}\n{detail}"
        )
    return proc


# ---------------------------------------------------------------------------
# 파일 트리
# ---------------------------------------------------------------------------


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise ReplayRepoBuildError(f"원본 트리가 없습니다: {source}")
    shutil.copytree(source, destination, ignore=COPY_IGNORE, dirs_exist_ok=True)


def _clear_worktree(repo_dir: Path, output_root: Path) -> None:
    """`.git` 을 제외한 작업 트리 전체를 지운다 (answer 스냅샷으로 교체하기 전)."""
    resolved = ensure_within(repo_dir, output_root)
    for entry in resolved.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


# ---------------------------------------------------------------------------
# 빌드
# ---------------------------------------------------------------------------


def discover_cases(source_root: Path) -> list[str]:
    """`base/`·`answer/` 를 모두 가진 케이스 디렉토리 이름을 정렬해 돌려준다."""
    if not source_root.is_dir():
        raise ReplayRepoBuildError(f"원본 디렉토리가 없습니다: {source_root}")
    cases = []
    for entry in sorted(source_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / BASE_DIR_NAME).is_dir() or not (entry / ANSWER_DIR_NAME).is_dir():
            continue
        if not CASE_NAME_PATTERN.match(entry.name):
            raise ReplayRepoBuildError(
                f"케이스 이름이 허용 형식(^[a-z][a-z0-9_]*$)이 아닙니다: {entry.name}"
            )
        cases.append(entry.name)
    if not cases:
        raise ReplayRepoBuildError(
            f"{source_root} 아래에서 base/·answer/ 를 가진 케이스를 찾지 못했습니다."
        )
    return cases


def _commit_snapshot(repo_dir: Path, git_bin: str, message: str, date: str, tag: str) -> None:
    # --force: 사용자 전역 core.excludesFile 때문에 fixture 파일이 빠지는 것을 막는다.
    _run_git(["add", "--all", "--force", "--", "."], cwd=repo_dir, git_bin=git_bin)
    # --no-verify: 전역 hooksPath 에 걸린 훅이 빌드를 좌우하지 않게 한다.
    _run_git(
        ["commit", "--no-verify", "--allow-empty", "-m", message],
        cwd=repo_dir,
        git_bin=git_bin,
        env_extra={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )
    _run_git(["tag", tag], cwd=repo_dir, git_bin=git_bin)


def build_case(
    case: str,
    source_root: Path,
    output_root: Path,
    git_bin: str = GIT_BIN,
) -> Path:
    """케이스 하나를 빌드하고 생성된 repo 경로를 돌려준다."""
    if not CASE_NAME_PATTERN.match(case):
        raise ReplayRepoBuildError(
            f"케이스 이름이 허용 형식(^[a-z][a-z0-9_]*$)이 아닙니다: {case}"
        )
    case_source = source_root / case
    base_source = case_source / BASE_DIR_NAME
    answer_source = case_source / ANSWER_DIR_NAME
    if not base_source.is_dir() or not answer_source.is_dir():
        raise ReplayRepoBuildError(
            f"케이스에 {BASE_DIR_NAME}/ 와 {ANSWER_DIR_NAME}/ 가 모두 있어야 합니다: {case_source}"
        )

    repo_dir = reset_case_dir(output_root / case, output_root)

    _copy_tree(base_source, repo_dir)
    _run_git(["init", "-q"], cwd=repo_dir, git_bin=git_bin)
    _commit_snapshot(
        repo_dir,
        git_bin,
        message=f"{case}: base snapshot",
        date=BASE_COMMIT_DATE,
        tag=f"{case}/{BASE_DIR_NAME}",
    )

    _clear_worktree(repo_dir, output_root)
    _copy_tree(answer_source, repo_dir)
    _commit_snapshot(
        repo_dir,
        git_bin,
        message=f"{case}: answer snapshot",
        date=ANSWER_COMMIT_DATE,
        tag=f"{case}/{ANSWER_DIR_NAME}",
    )
    return repo_dir


def build_replay_repos(
    source_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    git_bin: Optional[str] = None,
    cases: Optional[Iterable[str]] = None,
) -> list[Path]:
    """전체(또는 지정) 케이스를 빌드하고 생성된 repo 경로 목록을 돌려준다.

    기본값은 모듈 상수를 **호출 시점에** 읽는다 — 테스트가 tmp_path 를 넘겨
    저장소의 `replay_repos/` 를 건드리지 않게 하기 위해서다.
    """
    resolved_source = Path(source_root) if source_root is not None else SOURCE_ROOT
    resolved_output = Path(output_root) if output_root is not None else OUTPUT_ROOT
    resolved_git = git_bin if git_bin is not None else GIT_BIN

    available = discover_cases(resolved_source)
    if cases is None:
        selected = available
    else:
        selected = list(cases)
        unknown = [case for case in selected if case not in available]
        if unknown:
            raise ReplayRepoBuildError(
                f"알 수 없는 케이스: {', '.join(unknown)} (사용 가능: {', '.join(available)})"
            )

    resolved_output.mkdir(parents=True, exist_ok=True)
    return [
        build_case(case, resolved_source, resolved_output, resolved_git) for case in selected
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_replay_repos.py",
        description=(
            "replay_sources/ 의 base·answer 파일 트리로 replay mock git repo를 빌드한다. "
            "출력 경로는 evaluation/fixtures/replay_repos/ 로 고정되어 있다."
        ),
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        metavar="CASE",
        help="빌드할 케이스 이름 (반복 지정 가능, 생략 시 전체)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="성공 로그를 출력하지 않는다")
    args = parser.parse_args(argv)

    try:
        built = build_replay_repos(cases=args.cases)
    except ReplayRepoBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        for repo_dir in built:
            try:
                shown = repo_dir.relative_to(PROJECT_ROOT)
            except ValueError:
                shown = repo_dir
            print(f"built {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
