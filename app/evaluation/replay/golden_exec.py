"""replay 골든 테스트 실행 — HISTORICAL_REPLAY_SPEC §4-8·§9, ADR-011.

fixture 의 `golden_command` 를 **임시 worktree 안에서** 실행한다. replay 의 실행
계층이며(ARCHITECTURE.md 레이어 규칙), `git_cmd.py` 가 git 호출의 유일한 입구인
것처럼 이 모듈은 골든 명령 실행의 유일한 입구다.

## `app/golden.py::run_golden_tests` 를 재사용하지 않는 이유

그 함수는 명령 문자열을 셸에 넘긴다(`subprocess.run` 의 shell 모드). 거기서 받는 `cmd` 는
`config.golden_test_cmd`, 즉 **운영자가 `.env` 에 직접 넣는 값**이라 shell 이
타당하다. 반면 replay 의 `golden_command` 는 **fixture YAML 에서 오고 fixture 파일은
주고받을 수 있다** — 신뢰 수준이 다르다(ADR-011). 같은 경로로 흘리면 #0017 의
`GOLDEN_COMMAND_ALLOWLIST` 자체가 무의미해지므로, replay 는 shell 없이 인자 배열로
실행하는 별도 경로를 갖는다. 코드가 한 벌 늘어나는 것은 의도된 분리다.

## 이 모듈이 막는 것 (#0017 secscan 발견 #1 이월)

로더의 `_check_golden_command` 는 **첫 토큰(실행파일)만** allowlist 와 대조한다.
그래서 `mvn -f /other/pom.xml`, `pytest /other/dir`, `pytest -p <plugin>` 처럼
**인자로 실행 대상을 재지정**하는 값이 통과한다 — 허용된 도구가 replay 대상이 아닌
다른 프로젝트의 빌드 스크립트를 실행하게 된다. 여기서 실행 직전에 인자 수준 검증을
더하고, `cwd` 를 임시 worktree 로 고정한다.

allowlist 는 **`loader.py` 에서 import 한다**. 여기에 복제하면 두 곳이 되고, 한쪽만
고쳐질 때 우회가 생긴다.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from app.evaluation.replay.loader import GOLDEN_COMMAND_ALLOWLIST

# ---------------------------------------------------------------------------
# 인자 제약
# ---------------------------------------------------------------------------

FORBIDDEN_ARG_NAMES: frozenset[str] = frozenset(
    {
        "-f",
        "--file",
        "-p",
        "--plugin",
        "-C",
        "--project-dir",
        "--rootdir",
        "-b",
        "--build-file",
        "--settings",
        "-s",
    }
)
"""실행 대상을 재지정하는 옵션 — 도구가 무엇을 빌드/수집할지 바꾼다.

`mvn -f`/`--file`, `gradle -b`/`--build-file`/`-p`/`--project-dir`,
`pytest -p`(플러그인 로드)/`--rootdir`, `mvn -s`/`--settings`(다른 저장소·미러 설정)
가 여기 해당한다. 이 옵션들이 통과하면 `cwd` 를 worktree 로 고정한 의미가 없다.

**부분일치로 판정하지 않는다** — `--fail-fast` 가 `-f` 에 걸려 정상 명령이 막힌다.
"""

_MAX_OUTPUT = 4000
"""리포트에 남길 출력 상한 — `app/golden.py::_MAX_OUTPUT` 과 같은 값·같은 취지.

뒷부분을 남긴다. 실패 원인은 대개 출력 끝에 있다.
"""

_LEAKED_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
"""골든 명령이 git 을 호출할 때 실제 저장소로 리다이렉트되는 것을 막는다.

`git_cmd.py::_git_env`·`scripts/build_replay_repos.py::_git_env` 와 같은 이유다.
빌드 도구는 대개 버전 문자열을 얻으려 git 을 부르는데, 상속된 값이 남아 있으면
worktree 가 아니라 원본 저장소를 읽는다.
"""

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
"""`report.py::GOLDEN_OK_STATUSES` 와 맞춘 어휘 — `passed`/`skipped` 만 합격이다."""


class GoldenCommandNotAllowed(ValueError):
    """인자 검증 위반 — 명령을 실행하기 전에 막은 경우.

    `git_cmd.GitCommandNotAllowed` 와 같은 자리의 예외다. `run_golden` 은 이 예외를
    밖으로 던지지 않고 `status="error"` 로 바꿔 돌려준다(아래 참조).
    """


def _matches(token: str, name: str) -> bool:
    """`token` 이 옵션 `name` 인지 — 정확 일치 또는 `name=...` 형태만.

    `git_cmd._matches` 와 같은 규칙이다. 부분일치(`startswith(name)`)를 쓰면
    `--fail-fast` 가 `-f` 에, `--project-version` 이 `-p` 에 걸린다.
    """
    return token == name or token.startswith(f"{name}=")


def _path_like_parts(token: str) -> list[str]:
    """경로 판정에 쓸 문자열 조각 — `--opt=값` 이면 값 쪽도 함께 본다.

    `--rootdir=/other` 처럼 옵션의 **값으로 붙은** 절대경로를 놓치지 않기 위해서다.
    `-Dtest=X` 같은 정상 형태는 값이 경로가 아니므로 아래 검사에 걸리지 않는다.
    """
    if "=" in token:
        name, _, value = token.partition("=")
        return [name, value]
    return [token]


def _has_parent_escape(text: str) -> bool:
    """`..` 를 **경로 구성요소로** 포함하는지 — `..` 문자열 포함이 아니다.

    문자열 포함으로 보면 `-Dtest=Foo..Bar` 같은 값이 막힌다. 구분자는 `/` 와 `\\`
    양쪽을 본다(fixture 가 어느 쪽으로 적혀 오든 같게 판정한다).
    """
    return ".." in text.replace("\\", "/").split("/")


def validate_golden_args(tokens: Sequence[str]) -> None:
    """실행 직전 인자 검증 — 위반이면 `GoldenCommandNotAllowed`.

    거부 규칙:

    1. 실행파일(첫 토큰)이 `GOLDEN_COMMAND_ALLOWLIST` 밖 — 로더가 이미 보는 항목이지만
       **다시 확인한다**. fixture 객체는 로더를 거치지 않고 직접 구성될 수 있고(테스트·
       프로그램 경로), 그때 allowlist 가 통째로 비어버리면 안 된다(이중 검사).
    2. 절대경로 인자 — `--opt=/abs` 형태 포함.
    3. `..` 구성요소를 포함하는 경로 토큰 — worktree 밖으로 나간다.
    4. `FORBIDDEN_ARG_NAMES` 의 대상 재지정 옵션 — 정확 이름 또는 `이름=...` 형태.
    """
    tokens = list(tokens)
    if not tokens:
        raise GoldenCommandNotAllowed("골든 명령이 비어 있습니다.")
    if any(not isinstance(token, str) for token in tokens):
        raise GoldenCommandNotAllowed("골든 명령 인자는 모두 문자열이어야 합니다.")

    executable = tokens[0]
    if executable not in GOLDEN_COMMAND_ALLOWLIST:
        allowed = ", ".join(sorted(GOLDEN_COMMAND_ALLOWLIST))
        raise GoldenCommandNotAllowed(
            f"허용되지 않은 골든 실행파일입니다: {executable!r} (허용: {allowed})"
        )

    for token in tokens[1:]:
        for name in FORBIDDEN_ARG_NAMES:
            if _matches(token, name):
                raise GoldenCommandNotAllowed(
                    f"실행 대상을 재지정하는 옵션은 사용할 수 없습니다: {token}"
                )
        for part in _path_like_parts(token):
            if part.startswith("/"):
                raise GoldenCommandNotAllowed(
                    f"절대경로 인자는 사용할 수 없습니다: {token}"
                )
            if _has_parent_escape(part):
                raise GoldenCommandNotAllowed(
                    f"'..' 를 포함하는 경로 인자는 사용할 수 없습니다: {token}"
                )


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenResult:
    """골든 실행 한 건의 결과 — 스펙 §9 의 실패 유형 구분을 담는다.

    `output` 은 게이팅하지 않은 원본(상한만 적용)이다. `GOLDEN_OUTPUT` privacy 게이팅은
    저장 지점인 `report.py` 한 곳의 책임이므로(ARCHITECTURE.md), 여기서는 담아서
    돌려주기만 한다.
    """

    status: str  # passed | failed | error | skipped
    output: str
    duration_s: float
    exit_code: Optional[int] = None


def _golden_env() -> dict:
    """상속된 git 상태를 끊은 실행 환경 — `_LEAKED_GIT_ENV` 참조.

    나머지 환경변수는 그대로 물려준다. `mvn`/`gradle`/`pytest` 는 `JAVA_HOME`·`PATH`·
    로컬 캐시 경로에 의존하므로 환경을 비우면 회사·집 어느 쪽에서도 돌지 않는다.
    """
    env = os.environ.copy()
    for leaked in _LEAKED_GIT_ENV:
        env.pop(leaked, None)
    return env


def _clip(text: str) -> str:
    return (text or "").strip()[-_MAX_OUTPUT:]


def run_golden(
    command: Optional[str],
    worktree: Path,
    timeout_seconds: int,
) -> GoldenResult:
    """`command` 를 `worktree` 안에서 실행하고 결과를 돌려준다.

    - `command` 가 None/빈 값이면 `skipped` — fixture 가 골든을 지정하지 않은 경우다.
    - `shlex.split` → `validate_golden_args` → `subprocess.run(shell=False)`.
    - **`cwd` 는 항상 `worktree`** 다. 원본 repo·프로젝트 루트에서 빌드가 돌면 결과가
      무의미하고 부작용이 실제 저장소에 남는다(CLAUDE.md — 실제 repo 무변경).

    **예외를 밖으로 던지지 않는다.** 타임아웃·검증 위반·도구 부재·cwd 부재는 전부
    `status="error"` 로 표현한다 — 한 케이스의 실패가 replay 전체를 중단시키면 나머지
    fixture 결과를 잃기 때문이다(스펙 §9 는 실패 유형을 구분해 계속 진행할 것을
    전제한다). 원인은 `output` 에 남는다.
    """
    start = time.monotonic()

    def done(status: str, output: str, exit_code: Optional[int] = None) -> GoldenResult:
        return GoldenResult(
            status=status,
            output=_clip(output),
            duration_s=round(time.monotonic() - start, 3),
            exit_code=exit_code,
        )

    if command is None or not command.strip():
        return done(STATUS_SKIPPED, "golden_command 미지정 — 골든 테스트 생략")

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return done(STATUS_ERROR, f"골든 명령을 토큰화할 수 없습니다: {exc}")

    try:
        validate_golden_args(tokens)
    except GoldenCommandNotAllowed as exc:
        return done(STATUS_ERROR, f"골든 명령 인자 거부: {exc}")

    if timeout_seconds is None or timeout_seconds <= 0:
        return done(
            STATUS_ERROR, f"골든 timeout 은 양수여야 합니다: {timeout_seconds!r}"
        )

    work_dir = Path(worktree)
    if not work_dir.is_dir():
        # 절대경로를 메시지에 담지 않는다 — 회사 repo 경로가 리포트에 실릴 수 있다.
        return done(STATUS_ERROR, "골든 실행 디렉토리가 없습니다.")

    shown = " ".join(tokens)
    try:
        proc = subprocess.run(  # noqa: S603 - 검증 통과 인자 배열, shell 미사용
            tokens,
            cwd=str(work_dir),
            env=_golden_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # 스펙 §9 "golden timeout" — 실패(failed)가 아니라 실행 환경 문제(error)다.
        return done(
            STATUS_ERROR, f"골든 테스트 타임아웃 ({timeout_seconds}s): {shown}"
        )
    except FileNotFoundError:
        return done(STATUS_ERROR, f"골든 실행 파일을 찾을 수 없습니다: {tokens[0]!r}")
    except OSError as exc:
        return done(STATUS_ERROR, f"골든 실행 실패: {exc}")

    output = ((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")).strip()
    status = STATUS_PASSED if proc.returncode == 0 else STATUS_FAILED
    return done(
        status,
        output or f"(출력 없음, exit {proc.returncode})",
        exit_code=proc.returncode,
    )
