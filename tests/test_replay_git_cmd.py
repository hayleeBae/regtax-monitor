"""Issue #0018 replay git wrapper 테스트 — HISTORICAL_REPLAY_SPEC §4·§5, ADR-011.

실제 git 을 쓰는 테스트는 `tmp_path` 에 새로 만든 repo 에서만 수행한다 — 저장소나
`REPO_ROOT` 를 가리키는 어떤 경로도 건드리지 않는다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.evaluation.replay.git_cmd import (
    ALLOWED_SUBCOMMANDS,
    ALLOWED_WORKTREE_ACTIONS,
    DEFAULT_TIMEOUT_SECONDS,
    GitCommandError,
    GitCommandNotAllowed,
    diff_name_status,
    rev_parse,
    run_git,
    show_file,
    validate_git_args,
    worktree_add,
    worktree_prune,
    worktree_remove,
)

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 실행 파일이 없는 환경"
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _raw_git(repo: Path, *args: str) -> str:
    """테스트 fixture 준비용 직접 호출 — 프로덕션 경로가 아니라 준비 코드다."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=replay test",
            "-c",
            "user.email=replay@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        check=True,
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """커밋 2개짜리 임시 repo — base 태그와 answer 태그를 붙여 둔다."""
    root = tmp_path / "source_repo"
    root.mkdir()
    _raw_git(root, "init", "-q")
    (root / "calc.py").write_text("LIMIT = 150000\n", encoding="utf-8")
    _raw_git(root, "add", "--all", "--force", "--", ".")
    _raw_git(root, "commit", "--no-verify", "-m", "base")
    _raw_git(root, "tag", "case/base")
    (root / "calc.py").write_text("LIMIT = 250000\n", encoding="utf-8")
    (root / "README.md").write_text("noise\n", encoding="utf-8")
    _raw_git(root, "add", "--all", "--force", "--", ".")
    _raw_git(root, "commit", "--no-verify", "-m", "answer")
    _raw_git(root, "tag", "case/answer")
    return root


# ---------------------------------------------------------------------------
# allowlist — 통과 (스펙 §5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["rev-parse", "HEAD"],
        ["rev-parse", "--verify", "case/base"],
        ["cat-file", "-p", "HEAD"],
        ["status", "--porcelain"],
        ["diff", "--name-status", "case/base", "case/answer"],
        ["show", "case/base:calc.py"],
        ["worktree", "add", "--detach", "/tmp/wt", "case/base"],
        ["worktree", "remove", "--force", "/tmp/wt"],
        ["worktree", "prune"],
        ["apply", "--check", "proposal.patch"],
        ["apply", "proposal.patch"],
        ["diff", "--name-only", "case/base", "case/answer", "--", "calc.py"],
    ],
)
def test_allowlist_accepts_spec_commands(args):
    assert validate_git_args(args) == args


def test_allowlist_matches_spec_subcommands():
    assert ALLOWED_SUBCOMMANDS == {
        "rev-parse",
        "cat-file",
        "diff",
        "show",
        "worktree",
        "status",
        "apply",
    }
    assert ALLOWED_WORKTREE_ACTIONS == {"add", "remove", "prune"}


# ---------------------------------------------------------------------------
# allowlist — 거부
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["clone", "https://example.invalid/x.git"],
        ["fetch", "origin"],
        ["commit", "-m", "x"],
        ["tag", "v1"],
        ["config", "user.name", "x"],
        ["remote", "add", "origin", "https://example.invalid/x.git"],
        ["stash"],
        ["init"],
        ["add", "--all"],
    ],
)
def test_allowlist_rejects_unlisted_subcommands(args):
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(args)


# 스펙 §4 의 핵심 안전 제약 — source working tree 훼손 경로는 개별로 고정한다.


def test_rejects_checkout():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["checkout", "case/base"])


def test_rejects_reset():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["reset", "--hard", "HEAD~1"])


def test_rejects_clean():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["clean", "-fd"])


def test_rejects_push():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["push", "origin", "main"])


@pytest.mark.parametrize("action", ["list", "lock", "unlock", "move", "repair"])
def test_rejects_unlisted_worktree_actions(action):
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["worktree", action])


def test_rejects_bare_worktree():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["worktree"])


@pytest.mark.parametrize("option", ["--index", "--cached", "-3", "--3way"])
def test_rejects_apply_index_options(option):
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["apply", option, "proposal.patch"])


@pytest.mark.parametrize(
    "args",
    [
        ["apply", "--directory=/tmp", "proposal.patch"],
        ["apply", "--directory", "/tmp", "proposal.patch"],
        ["apply", "--unsafe-paths", "proposal.patch"],
        ["apply", "--unsafe-paths", "--directory=/", "proposal.patch"],
    ],
)
def test_rejects_apply_escaping_worktree(args):
    """`apply` 가 스크래치 worktree 밖에 쓰는 것을 막는다.

    `--unsafe-paths --directory=<경로>` 조합은 patch 안의 경로 제약을 풀어 `cwd` 로
    고정한 임시 worktree 밖에 파일을 쓰는 통로가 된다 — 자동 적용 금지(CLAUDE.md)와
    "생성 diff 는 worktree 안에만 적용한다"(ADR-011)를 우회하는 경로다.
    """
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(args)


@pytest.mark.parametrize(
    "args",
    [
        ["diff", "--output=/tmp/leak.txt", "A", "B"],
        ["diff", "-o", "/tmp/leak.txt", "A", "B"],
        ["show", "--output=/tmp/leak.txt", "HEAD"],
        ["status", "--output=/tmp/leak.txt"],
    ],
)
def test_rejects_output_redirection(args):
    """읽기 명령이 쓰기 명령으로 바뀌는 것을 막는다.

    `git diff --output=<경로>` 는 임의 경로에 파일을 만든다. `apply` 를 제외한 허용
    서브커맨드는 전부 읽기 전용이어야 하므로 출력 리다이렉션은 서브커맨드와 무관하게
    거부한다.
    """
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(args)


@pytest.mark.parametrize(
    "args",
    [
        ["diff", "-c", "user.name=x"],
        ["status", "--git-dir=/other/.git"],
        ["status", "--work-tree=/other"],
        ["diff", "-C", "/other"],
        ["diff", "--upload-pack=evil"],
        ["diff", "--exec-path=/tmp"],
        ["diff", "--exec-path", "/tmp"],
        ["show", "--namespace=x", "HEAD"],
        ["apply", "--receive-pack=evil", "proposal.patch"],
    ],
)
def test_rejects_forbidden_args(args):
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(args)


@pytest.mark.parametrize(
    "args",
    [
        ["diff", "--diff-filter=M", "case/base", "case/answer"],
        ["show", "--stat", "HEAD"],
        ["diff", "--cached"],
        ["diff", "--check"],
        ["status", "--column"],
        ["diff", "-C50", "case/base", "case/answer"],
    ],
)
def test_partial_match_does_not_block_normal_options(args):
    """금지 인자 검사는 정확 일치 또는 `이름=` 접두만 본다."""
    assert validate_git_args(args) == args


def test_rejects_empty_args():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args([])


def test_rejects_non_string_args():
    with pytest.raises(GitCommandNotAllowed):
        validate_git_args(["rev-parse", 123])


def test_run_git_rejects_before_executing(tmp_path, monkeypatch):
    """거부되는 명령은 `subprocess` 까지 가지 않는다."""

    def _fail(*args, **kwargs):  # pragma: no cover - 호출되면 테스트 실패
        raise AssertionError("allowlist 위반인데 subprocess 가 실행되었다")

    monkeypatch.setattr(subprocess, "run", _fail)
    with pytest.raises(GitCommandNotAllowed):
        run_git(["checkout", "case/base"], cwd=tmp_path)


def test_no_public_escape_hatch():
    """검사를 건너뛰는 공개 함수가 없어야 한다 (ADR-011)."""
    from app.evaluation.replay import git_cmd

    public = {
        name
        for name in dir(git_cmd)
        if not name.startswith("_") and callable(getattr(git_cmd, name))
    }
    assert not {name for name in public if "raw" in name.lower()}


# ---------------------------------------------------------------------------
# 실행 규칙
# ---------------------------------------------------------------------------


def test_timeout_becomes_git_command_error(tmp_path, monkeypatch):
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(GitCommandError) as excinfo:
        run_git(["status", "--porcelain"], cwd=tmp_path, timeout=1)
    assert "1초" in str(excinfo.value)


@requires_git
def test_timeout_with_slow_git_bin(tmp_path):
    """가짜 git 실행파일이 느릴 때도 타임아웃이 예외로 변환된다."""
    fake_git = tmp_path / "slow-git"
    fake_git.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    fake_git.chmod(0o755)
    with pytest.raises(GitCommandError) as excinfo:
        run_git(["status"], cwd=tmp_path, timeout=1, git_bin=str(fake_git))
    assert "끝나지 않았습니다" in str(excinfo.value)


def test_default_timeout_is_applied(tmp_path, monkeypatch):
    captured = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(subprocess, "run", _capture)
    run_git(["status", "--porcelain"], cwd=tmp_path)
    assert captured["timeout"] == DEFAULT_TIMEOUT_SECONDS
    assert captured["shell"] is False


def test_non_positive_timeout_is_rejected(tmp_path):
    with pytest.raises(GitCommandError):
        run_git(["status"], cwd=tmp_path, timeout=0)


def test_inherited_git_env_is_stripped(tmp_path, monkeypatch):
    """상속된 GIT_DIR 등이 제거되어 실제 저장소로 리다이렉트되지 않는다."""
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/elsewhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "/elsewhere/index")
    captured = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(subprocess, "run", _capture)
    run_git(["status", "--porcelain"], cwd=tmp_path)
    env = captured["env"]
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env


def test_missing_git_bin_reports_install_hint(tmp_path):
    with pytest.raises(GitCommandError) as excinfo:
        run_git(["status"], cwd=tmp_path, git_bin=str(tmp_path / "no-such-git"))
    message = str(excinfo.value)
    assert "git 설치" in message


def test_missing_cwd_is_reported(tmp_path):
    with pytest.raises(GitCommandError) as excinfo:
        run_git(["status"], cwd=tmp_path / "absent")
    assert "실행 디렉토리" in str(excinfo.value)


def test_long_stderr_is_clipped(tmp_path, monkeypatch):
    def _fail(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "", "x" * 10000)

    monkeypatch.setattr(subprocess, "run", _fail)
    with pytest.raises(GitCommandError) as excinfo:
        run_git(["status"], cwd=tmp_path)
    assert len(excinfo.value.stderr) == 4000
    assert excinfo.value.returncode == 1


# ---------------------------------------------------------------------------
# 실제 git — 임시 repo 에서만
# ---------------------------------------------------------------------------


@requires_git
def test_rev_parse_resolves_tag(repo: Path):
    sha = rev_parse("case/base", cwd=repo)
    assert len(sha) == 40


@requires_git
def test_status_is_clean(repo: Path):
    proc = run_git(["status", "--porcelain"], cwd=repo)
    assert proc.stdout.strip() == ""


@requires_git
def test_diff_name_status_lists_changed_files(repo: Path):
    output = diff_name_status("case/base", "case/answer", cwd=repo)
    changed = {line.split("\t")[-1] for line in output.strip().splitlines()}
    assert changed == {"calc.py", "README.md"}


@requires_git
def test_show_file_reads_base_revision(repo: Path):
    assert show_file("case/base", "calc.py", cwd=repo) == "LIMIT = 150000\n"


@requires_git
def test_check_false_returns_completed_process(repo: Path):
    proc = run_git(["show", "no-such-rev:calc.py"], cwd=repo, check=False)
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode != 0


@requires_git
def test_check_true_raises_on_failure(repo: Path):
    with pytest.raises(GitCommandError) as excinfo:
        run_git(["show", "no-such-rev:calc.py"], cwd=repo)
    assert excinfo.value.returncode not in (None, 0)


@requires_git
def test_apply_check_failure_is_information_not_exception(repo: Path, tmp_path: Path):
    patch = tmp_path / "bad.patch"
    patch.write_text(
        "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-NOT_THERE = 1\n+NOT_THERE = 2\n",
        encoding="utf-8",
    )
    proc = run_git(["apply", "--check", str(patch)], cwd=repo, check=False)
    assert proc.returncode != 0


@requires_git
def test_worktree_lifecycle_leaves_source_untouched(repo: Path, tmp_path: Path):
    head_before = rev_parse("HEAD", cwd=repo)
    worktree = tmp_path / "wt"

    worktree_add(worktree, "case/base", cwd=repo)
    assert (worktree / "calc.py").read_text(encoding="utf-8") == "LIMIT = 150000\n"

    worktree_remove(worktree, cwd=repo)
    worktree_prune(cwd=repo)

    assert not worktree.exists()
    assert rev_parse("HEAD", cwd=repo) == head_before
    assert run_git(["status", "--porcelain"], cwd=repo).stdout.strip() == ""
    assert (repo / "calc.py").read_text(encoding="utf-8") == "LIMIT = 250000\n"
