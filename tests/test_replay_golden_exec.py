"""Issue #0018 replay 골든 실행 테스트 — HISTORICAL_REPLAY_SPEC §4-8·§9·§11.

초점은 **#0017 secscan 발견 #1**(golden_command 인자 미검증)이다. 로더는 첫 토큰만
보므로 `mvn -f /other/pom.xml` 류가 통과했다 — 그 값들이 여기서 막히는지, 반대로
정상 명령이 부분일치로 잘못 막히지 않는지를 각각 고정한다.

실제 실행 케이스는 `tmp_path` 안에서만 돌고, allowlist 밖 명령을 쓰려고 검증을
우회하지 않는다(allowlist 안의 `pytest` 로 구성한다). 무거운 의존성(임베딩·LLM·
ChromaDB)은 import 하지 않는다.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from app.evaluation.replay import golden_exec as ge
from app.evaluation.replay.golden_exec import (
    GoldenCommandNotAllowed,
    GoldenResult,
    run_golden,
    validate_golden_args,
)
from app.evaluation.replay.loader import GOLDEN_COMMAND_ALLOWLIST

requires_pytest_bin = pytest.mark.skipif(
    shutil.which("pytest") is None, reason="pytest 실행 파일이 없는 환경"
)


def _tokens(command: str) -> list[str]:
    return shlex.split(command)


# ---------------------------------------------------------------------------
# 인자 검증 — 거부 (#0017 secscan 발견 #1)
# ---------------------------------------------------------------------------


def test_rejects_mvn_file_option_with_absolute_path():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("mvn -f /other/pom.xml test"))


def test_rejects_mvn_file_long_option_equals_form():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("mvn --file=/other/pom.xml test"))


def test_rejects_absolute_path_argument():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("pytest /abs/dir"))


def test_rejects_pytest_plugin_option():
    """`-p` 는 임의 플러그인을 로드시켜 대상 밖 코드를 실행하는 통로다."""
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("pytest -p evil_plugin"))


def test_rejects_gradle_build_file_option():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("gradle -b /other/build.gradle test"))


def test_rejects_pytest_rootdir_option():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("pytest --rootdir=/other"))


def test_rejects_mvn_settings_option():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("mvn -s /other/settings.xml test"))


def test_rejects_parent_escape_path():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("pytest ../../outside"))


def test_rejects_parent_escape_inside_option_value():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("pytest --ignore=../outside"))


def test_rejects_long_plugin_and_project_dir_options():
    for command in (
        "pytest --plugin evil",
        "gradle --project-dir other",
        "gradle --build-file build.gradle",
        "mvn --settings settings.xml",
    ):
        with pytest.raises(GoldenCommandNotAllowed):
            validate_golden_args(_tokens(command))


def test_rejects_empty_tokens():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args([])


def test_rejects_non_string_tokens():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(["pytest", 3])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# 인자 검증 — 실행파일 재확인 (로더를 거치지 않는 호출 경로 방어)
# ---------------------------------------------------------------------------


def test_rejects_executable_outside_allowlist():
    for command in ("rm -rf .", "bash -c 'echo x'", "sh script.sh", "curl example"):
        with pytest.raises(GoldenCommandNotAllowed):
            validate_golden_args(_tokens(command))


def test_rejects_path_executable():
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("/usr/bin/mvn test"))


def test_allowlist_is_imported_not_copied():
    """allowlist 를 이 모듈에 복제하지 않는다 — 두 곳이 되면 한쪽만 고쳐진다."""
    assert ge.GOLDEN_COMMAND_ALLOWLIST is GOLDEN_COMMAND_ALLOWLIST
    source = Path(ge.__file__).read_text(encoding="utf-8")
    assert "GOLDEN_COMMAND_ALLOWLIST: frozenset" not in source


def test_executable_check_follows_loader_allowlist(monkeypatch):
    """실행파일 판정의 단일 출처가 로더인지 — 목록을 비우면 전부 거부된다."""
    monkeypatch.setattr(ge, "GOLDEN_COMMAND_ALLOWLIST", frozenset())
    with pytest.raises(GoldenCommandNotAllowed):
        validate_golden_args(_tokens("mvn test"))


# ---------------------------------------------------------------------------
# 인자 검증 — 정상 통과 (부분일치로 막히지 않는지)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "mvn -q test -Dtest=X",
        "pytest -k pattern",
        "pytest tests/golden",
        "./gradlew test --tests '*Golden*'",
        "mvn --fail-fast test",
        "gradle test --stacktrace",
        "pytest -q tests/golden --maxfail=1",
        "mvn -q -Dtest=GoldenTaxTest test",
    ],
)
def test_accepts_normal_commands(command):
    validate_golden_args(_tokens(command))


def test_partial_match_does_not_block_valid_options():
    """`--fail-fast` 가 `-f` 부분일치에 걸리면 정상 명령이 막힌다."""
    validate_golden_args(_tokens("mvn --fail-fast --file-name-only-lookalike test"))


def test_dotted_value_is_not_parent_escape():
    """`..` 문자열 포함이 아니라 경로 구성요소로만 판정한다."""
    validate_golden_args(_tokens("mvn -Dtest=Foo..Bar test"))


# ---------------------------------------------------------------------------
# run_golden — skipped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [None, "", "   "])
def test_missing_command_is_skipped(command, tmp_path):
    result = run_golden(command, tmp_path, timeout_seconds=10)
    assert isinstance(result, GoldenResult)
    assert result.status == "skipped"
    assert result.exit_code is None


# ---------------------------------------------------------------------------
# run_golden — 검증 위반은 예외가 아니라 error 결과
# ---------------------------------------------------------------------------


def test_rejected_command_returns_error_without_executing(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("검증 위반인데 실행되었다")

    monkeypatch.setattr(ge.subprocess, "run", _boom)
    result = run_golden("mvn -f /other/pom.xml test", tmp_path, timeout_seconds=10)
    assert result.status == "error"
    assert result.exit_code is None


def test_disallowed_executable_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ge.subprocess,
        "run",
        lambda *a, **k: pytest.fail("allowlist 밖 실행파일이 실행되었다"),
    )
    assert run_golden("bash -c 'id'", tmp_path, timeout_seconds=10).status == "error"


def test_unparseable_command_returns_error(tmp_path):
    result = run_golden('pytest "unclosed', tmp_path, timeout_seconds=10)
    assert result.status == "error"


# ---------------------------------------------------------------------------
# run_golden — 실행 파라미터 (cwd 고정 · shell 미사용 · git 환경 차단)
# ---------------------------------------------------------------------------


def _capture_run(monkeypatch) -> dict:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(ge.subprocess, "run", fake_run)
    return seen


def test_runs_in_worktree_without_shell(tmp_path, monkeypatch):
    seen = _capture_run(monkeypatch)
    result = run_golden("pytest -q tests/golden", tmp_path, timeout_seconds=42)

    assert result.status == "passed"
    assert seen["cmd"] == ["pytest", "-q", "tests/golden"]  # 문자열이 아닌 인자 배열
    assert seen["shell"] is False
    assert seen["cwd"] == str(tmp_path)
    assert seen["timeout"] == 42


def test_source_has_no_shell_true():
    source = Path(ge.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source


def test_strips_inherited_git_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "/somewhere/.git/index")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    seen = _capture_run(monkeypatch)

    run_golden("pytest -q", tmp_path, timeout_seconds=10)

    env = seen["env"]
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert "PATH" in env  # 나머지 환경은 물려준다


def test_missing_worktree_directory_is_error(tmp_path):
    missing = tmp_path / "no_such_worktree"
    result = run_golden("pytest -q", missing, timeout_seconds=10)
    assert result.status == "error"
    assert str(missing) not in result.output  # 절대경로를 리포트에 남기지 않는다


def test_non_positive_timeout_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ge.subprocess, "run", lambda *a, **k: pytest.fail("timeout 없이 실행되었다")
    )
    assert run_golden("pytest -q", tmp_path, timeout_seconds=0).status == "error"


# ---------------------------------------------------------------------------
# run_golden — 타임아웃 (스펙 §9)
# ---------------------------------------------------------------------------


def test_timeout_becomes_error_result_not_exception(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(ge.subprocess, "run", fake_run)

    result = run_golden("pytest -q", tmp_path, timeout_seconds=3)

    assert result.status == "error"
    assert result.exit_code is None
    assert "타임아웃" in result.output


def test_missing_tool_becomes_error_result(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd)

    monkeypatch.setattr(ge.subprocess, "run", fake_run)
    assert run_golden("gradle test", tmp_path, timeout_seconds=10).status == "error"


# ---------------------------------------------------------------------------
# run_golden — 출력 상한
# ---------------------------------------------------------------------------


def test_output_is_clipped_to_tail(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="A" * 9000 + "TAIL", stderr="")

    monkeypatch.setattr(ge.subprocess, "run", fake_run)

    result = run_golden("pytest -q", tmp_path, timeout_seconds=10)

    assert result.status == "failed"
    assert len(result.output) == ge._MAX_OUTPUT
    assert result.output.endswith("TAIL")  # 실패 원인은 출력 끝에 있다


# ---------------------------------------------------------------------------
# 실제 실행 — allowlist 안의 실행파일(pytest)로만 구성한다
# ---------------------------------------------------------------------------


def _write_case(worktree: Path, body: str) -> None:
    (worktree / "tests").mkdir(parents=True, exist_ok=True)
    (worktree / "tests" / "test_golden_case.py").write_text(body, encoding="utf-8")


@requires_pytest_bin
def test_real_execution_passing_case(tmp_path):
    _write_case(tmp_path, "def test_ok():\n    assert 1 + 1 == 2\n")

    result = run_golden("pytest -q tests", tmp_path, timeout_seconds=120)

    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.duration_s >= 0


@requires_pytest_bin
def test_real_execution_failing_case(tmp_path):
    _write_case(tmp_path, "def test_bad():\n    assert 1 == 2\n")

    result = run_golden("pytest -q tests", tmp_path, timeout_seconds=120)

    assert result.status == "failed"
    assert result.exit_code not in (0, None)


@requires_pytest_bin
def test_real_execution_uses_worktree_as_cwd(tmp_path):
    """cwd 가 worktree 가 아니면 대상 밖 테스트가 수집되어 결과가 달라진다."""
    _write_case(
        tmp_path,
        "from pathlib import Path\n\n"
        f"def test_cwd():\n    assert Path.cwd() == Path({str(tmp_path)!r})\n",
    )

    result = run_golden("pytest -q tests", tmp_path, timeout_seconds=120)

    assert result.status == "passed"
