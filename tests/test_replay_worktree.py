"""Issue #0018 replay worktree 수명주기 테스트 — HISTORICAL_REPLAY_SPEC §4·§9·§11.

실제 git 을 쓰지만 대상은 `tmp_path` 에 새로 만든 repo 뿐이다 — 이 저장소나
`REPO_ROOT` 를 가리키는 어떤 경로도 건드리지 않는다. 무거운 의존성(임베딩·LLM·
ChromaDB)은 import 하지 않는다.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.evaluation.replay import worktree as wt
from app.evaluation.replay.fixture import ReplayRepository
from app.evaluation.replay.git_cmd import GitCommandError

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 실행 파일이 없는 환경"
)

pytestmark = requires_git


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _raw_git(repo: Path, *args: str) -> str:
    """테스트 fixture 준비·관찰용 직접 호출 — 프로덕션 경로가 아니라 준비 코드다.

    `worktree list` 는 프로덕션 allowlist 에 없으므로(필요하지 않다) 원본 무변경
    관찰은 여기서 한다.
    """
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
    """커밋 2개(base/answer 태그)짜리 임시 repo."""
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


def _snapshot(repo: Path) -> tuple:
    """원본 무변경 판정에 쓰는 관찰값 — 상태·HEAD·worktree 목록."""
    return (
        _raw_git(repo, "status", "--porcelain"),
        _raw_git(repo, "rev-parse", "HEAD"),
        _raw_git(repo, "worktree", "list"),
    )


def _temp_roots() -> set:
    """현재 남아 있는 replay 임시 root 이름 집합."""
    system_tmp = Path(tempfile.gettempdir())
    return {p.name for p in system_tmp.glob(f"{wt.TEMP_PREFIX}*")}


class _Boom(RuntimeError):
    """본문에서 던지는 테스트 전용 예외."""


# ---------------------------------------------------------------------------
# 1) repo 경로 해석
# ---------------------------------------------------------------------------


def test_resolve_repo_path_relative(repo: Path) -> None:
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="case/base",
        answer_commit="case/answer",
        path="source_repo",
    )
    resolved = wt.resolve_repo_path(repository, repo.parent)
    assert resolved.resolve() == repo.resolve()


def test_resolve_repo_path_rejects_missing_declaration(tmp_path: Path) -> None:
    repository = ReplayRepository(
        source_type="local_git", base_commit="a", answer_commit="b"
    )
    with pytest.raises(wt.RepoPathError):
        wt.resolve_repo_path(repository, tmp_path)


def test_resolve_repo_path_rejects_both_declarations(tmp_path: Path) -> None:
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="a",
        answer_commit="b",
        path="source_repo",
        path_env="EHR_REPO_ROOT",
    )
    with pytest.raises(wt.RepoPathError):
        wt.resolve_repo_path(repository, tmp_path)


def test_resolve_repo_path_rejects_traversal(tmp_path: Path) -> None:
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="a",
        answer_commit="b",
        path="../outside",
    )
    with pytest.raises(wt.RepoPathError):
        wt.resolve_repo_path(repository, tmp_path)


def test_resolve_repo_path_rejects_non_git_dir(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    repository = ReplayRepository(
        source_type="local_git", base_commit="a", answer_commit="b", path="plain"
    )
    with pytest.raises(wt.RepoPathError):
        wt.resolve_repo_path(repository, tmp_path)


def test_path_env_unset_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPLAY_TEST_REPO_ROOT", raising=False)
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="case/base",
        answer_commit="case/answer",
        path_env="REPLAY_TEST_REPO_ROOT",
    )
    with pytest.raises(wt.RepoPathError) as excinfo:
        wt.resolve_repo_path(repository, Path("."))
    assert "REPLAY_TEST_REPO_ROOT" in str(excinfo.value)


def test_path_env_empty_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_TEST_REPO_ROOT", "   ")
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="case/base",
        answer_commit="case/answer",
        path_env="REPLAY_TEST_REPO_ROOT",
    )
    with pytest.raises(wt.RepoPathError):
        wt.resolve_repo_path(repository, Path("."))


def test_path_env_resolves_when_set(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPLAY_TEST_REPO_ROOT", str(repo))
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="case/base",
        answer_commit="case/answer",
        path_env="REPLAY_TEST_REPO_ROOT",
    )
    resolved = wt.resolve_repo_path(repository, Path("."))
    assert resolved.resolve() == repo.resolve()


def test_path_env_error_message_has_no_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """회사 절대경로가 오류 메시지로 새면 안 된다 (CLAUDE.md 반출 금지)."""
    missing = tmp_path / "company" / "ehr_repo"
    monkeypatch.setenv("REPLAY_TEST_REPO_ROOT", str(missing))
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="case/base",
        answer_commit="case/answer",
        path_env="REPLAY_TEST_REPO_ROOT",
    )
    with pytest.raises(wt.RepoPathError) as excinfo:
        wt.resolve_repo_path(repository, Path("."))
    message = str(excinfo.value)
    assert str(missing) not in message
    assert "ehr_repo" not in message
    assert "REPLAY_TEST_REPO_ROOT" in message


def test_non_git_dir_error_message_has_no_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "company_plain"
    plain.mkdir()
    monkeypatch.setenv("REPLAY_TEST_REPO_ROOT", str(plain))
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="case/base",
        answer_commit="case/answer",
        path_env="REPLAY_TEST_REPO_ROOT",
    )
    with pytest.raises(wt.RepoPathError) as excinfo:
        wt.resolve_repo_path(repository, Path("."))
    assert str(plain) not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2) 사전 검증 — commit validation / dirty (스펙 §11)
# ---------------------------------------------------------------------------


def test_assert_commit_exists_returns_sha(repo: Path) -> None:
    sha = wt.assert_commit_exists(repo, "case/base")
    assert len(sha) == 40
    assert sha == _raw_git(repo, "rev-parse", "case/base^{commit}").strip()


def test_assert_commit_exists_rejects_unknown_revision(repo: Path) -> None:
    with pytest.raises(wt.CommitNotFoundError) as excinfo:
        wt.assert_commit_exists(repo, "case/nope")
    assert "case/nope" in str(excinfo.value)


def test_assert_commits_exist_maps_all_revisions(repo: Path) -> None:
    result = wt.assert_commits_exist(repo, ["case/base", "case/answer"])
    assert set(result) == {"case/base", "case/answer"}
    assert result["case/base"] != result["case/answer"]


def test_assert_commits_exist_reports_missing_one(repo: Path) -> None:
    with pytest.raises(wt.CommitNotFoundError):
        wt.assert_commits_exist(repo, ["case/base", "case/missing"])


def test_assert_clean_worktree_passes_on_clean_repo(repo: Path) -> None:
    assert wt.assert_clean_worktree(repo) is None


def test_assert_clean_worktree_rejects_dirty_repo(repo: Path) -> None:
    (repo / "calc.py").write_text("LIMIT = 999\n", encoding="utf-8")
    with pytest.raises(wt.DirtyWorktreeError):
        wt.assert_clean_worktree(repo)


def test_assert_clean_worktree_rejects_untracked_file(repo: Path) -> None:
    (repo / "scratch.txt").write_text("wip\n", encoding="utf-8")
    with pytest.raises(wt.DirtyWorktreeError):
        wt.assert_clean_worktree(repo)


def test_dirty_message_does_not_leak_file_names(repo: Path) -> None:
    (repo / "secret_payroll.java").write_text("x\n", encoding="utf-8")
    with pytest.raises(wt.DirtyWorktreeError) as excinfo:
        wt.assert_clean_worktree(repo)
    assert "secret_payroll" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3) worktree lifecycle (스펙 §11)
# ---------------------------------------------------------------------------


def test_worktree_lifecycle(repo: Path) -> None:
    with wt.replay_worktree(repo, "case/base") as work:
        assert work.is_dir()
        # base 시점 내용이어야 한다 — HEAD(answer) 가 아니다.
        assert (work / "calc.py").read_text(encoding="utf-8") == "LIMIT = 150000\n"
        assert not (work / "README.md").exists()
        tmp_root = work.parent
        assert tmp_root.name.startswith(wt.TEMP_PREFIX)
    assert not work.exists()
    assert not tmp_root.exists()


def test_worktree_is_detached(repo: Path) -> None:
    """브랜치를 만들거나 checkout 하지 않는다 — `--detach` 확인."""
    branches_before = _raw_git(repo, "branch", "--list")
    with wt.replay_worktree(repo, "case/base") as work:
        head = _raw_git(work, "rev-parse", "--abbrev-ref", "HEAD").strip()
        assert head == "HEAD"  # detached
    assert _raw_git(repo, "branch", "--list") == branches_before


def test_original_repo_unchanged(repo: Path) -> None:
    """스펙 §11 핵심 — 상태·HEAD·worktree 목록이 실행 전후로 같아야 한다."""
    before = _snapshot(repo)
    with wt.replay_worktree(repo, "case/base") as work:
        assert work.is_dir()
    after = _snapshot(repo)
    assert after == before
    assert (repo / "calc.py").read_text(encoding="utf-8") == "LIMIT = 250000\n"


def test_worktree_entries_do_not_leak(repo: Path) -> None:
    """`.git/worktrees/` 에 항목이 남지 않는다 (remove → prune)."""
    with wt.replay_worktree(repo, "case/base"):
        pass
    entries = repo / ".git" / "worktrees"
    assert not entries.exists() or not list(entries.iterdir())


def test_writes_inside_worktree_do_not_touch_original(repo: Path) -> None:
    with wt.replay_worktree(repo, "case/base") as work:
        (work / "calc.py").write_text("LIMIT = 777\n", encoding="utf-8")
        (work / "generated.txt").write_text("scratch\n", encoding="utf-8")
    assert (repo / "calc.py").read_text(encoding="utf-8") == "LIMIT = 250000\n"
    assert not (repo / "generated.txt").exists()
    assert _raw_git(repo, "status", "--porcelain") == ""


def test_entry_rejects_unknown_base_commit(repo: Path) -> None:
    before = _snapshot(repo)
    with pytest.raises(wt.CommitNotFoundError):
        with wt.replay_worktree(repo, "case/nope"):
            pytest.fail("진입해서는 안 된다")
    assert _snapshot(repo) == before


def test_entry_rejects_dirty_repo(repo: Path) -> None:
    """dirty 한 원본에서는 진입 자체가 실패하고 worktree 가 생기지 않는다."""
    (repo / "calc.py").write_text("LIMIT = 999\n", encoding="utf-8")
    roots_before = _temp_roots()
    with pytest.raises(wt.DirtyWorktreeError):
        with wt.replay_worktree(repo, "case/base"):
            pytest.fail("진입해서는 안 된다")
    assert "worktrees" not in _raw_git(repo, "worktree", "list")
    assert _temp_roots() == roots_before


# ---------------------------------------------------------------------------
# 4) exception cleanup (스펙 §11 — 로드맵 수용 기준)
# ---------------------------------------------------------------------------


def test_exception_inside_context_still_cleans_up(repo: Path) -> None:
    before = _snapshot(repo)
    captured = {}
    with pytest.raises(_Boom):
        with wt.replay_worktree(repo, "case/base") as work:
            captured["work"] = work
            captured["tmp_root"] = work.parent
            raise _Boom("파이프라인 실패")

    assert not captured["work"].exists()
    assert not captured["tmp_root"].exists()
    assert _snapshot(repo) == before


def test_keep_on_error_keeps_worktree(repo: Path) -> None:
    captured = {}
    try:
        with pytest.raises(_Boom):
            with wt.replay_worktree(repo, "case/base", keep_on_error=True) as work:
                captured["work"] = work
                raise _Boom("파이프라인 실패")
        assert captured["work"].is_dir()
        assert (captured["work"] / "calc.py").exists()
    finally:
        # 테스트가 남긴 흔적은 테스트가 회수한다.
        work = captured["work"]
        wt._cleanup(repo, work, work.parent)


def test_keep_on_error_still_cleans_up_on_success(repo: Path) -> None:
    """opt-in 은 '예외로 끝났을 때'만이다 — 정상 종료면 그대로 지운다."""
    with wt.replay_worktree(repo, "case/base", keep_on_error=True) as work:
        assert work.is_dir()
    assert not work.exists()
    assert not work.parent.exists()


def test_keep_on_error_defaults_to_false(repo: Path) -> None:
    captured = {}
    with pytest.raises(_Boom):
        with wt.replay_worktree(repo, "case/base") as work:
            captured["work"] = work
            raise _Boom("boom")
    assert not captured["work"].exists()


# ---------------------------------------------------------------------------
# 5) cleanup 실패 (스펙 §9)
# ---------------------------------------------------------------------------


def test_cleanup_failure_does_not_swallow_original_exception(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_remove(*args, **kwargs):
        raise GitCommandError("worktree remove 실패", returncode=1)

    monkeypatch.setattr(wt, "worktree_remove", _failing_remove)

    with pytest.raises(_Boom):
        with wt.replay_worktree(repo, "case/base"):
            raise _Boom("진짜 원인")

    # 남은 메타데이터는 테스트가 정리한다(monkeypatch 는 이미 풀린 뒤 fixture repo 도
    # tmp_path 라 폐기되지만, 흔적을 남기지 않는 편이 낫다).
    _raw_git(repo, "worktree", "prune")


def test_cleanup_failure_raises_when_body_succeeded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_remove(*args, **kwargs):
        raise GitCommandError("worktree remove 실패", returncode=1)

    monkeypatch.setattr(wt, "worktree_remove", _failing_remove)

    with pytest.raises(wt.WorktreeCleanupError):
        with wt.replay_worktree(repo, "case/base") as work:
            assert work.is_dir()

    _raw_git(repo, "worktree", "prune")


def test_cleanup_failure_message_has_no_absolute_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_remove(*args, **kwargs):
        raise GitCommandError(f"실패 — {repo}", returncode=1)

    monkeypatch.setattr(wt, "worktree_remove", _failing_remove)

    with pytest.raises(wt.WorktreeCleanupError) as excinfo:
        with wt.replay_worktree(repo, "case/base"):
            pass
    assert str(repo) not in str(excinfo.value)

    _raw_git(repo, "worktree", "prune")


def test_setup_failure_removes_temp_root(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`worktree add` 가 실패해도 임시 root 는 남지 않는다."""
    roots_before = _temp_roots()

    def _failing_add(*args, **kwargs):
        raise GitCommandError("worktree add 실패", returncode=128)

    monkeypatch.setattr(wt, "worktree_add", _failing_add)

    with pytest.raises(wt.WorktreeSetupError):
        with wt.replay_worktree(repo, "case/base"):
            pytest.fail("진입해서는 안 된다")

    assert _temp_roots() == roots_before


# ---------------------------------------------------------------------------
# 6) 임시 디렉토리 삭제 가드
# ---------------------------------------------------------------------------


def test_ensure_within_rejects_outside_and_root_itself(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "inner").mkdir(parents=True)
    assert wt.ensure_within(root / "inner", root) == (root / "inner").resolve()
    with pytest.raises(wt.ReplayWorktreeError):
        wt.ensure_within(root, root)
    with pytest.raises(wt.ReplayWorktreeError):
        wt.ensure_within(tmp_path / "other", root)


def test_temp_root_guard_rejects_foreign_directory(tmp_path: Path) -> None:
    """`tempfile` 이 만든 root 가 아니면 삭제하지 않는다."""
    victim = tmp_path / "important"
    victim.mkdir()
    with pytest.raises(wt.ReplayWorktreeError):
        wt._assert_temp_root(victim)
    assert victim.exists()


def test_temp_root_guard_rejects_wrong_prefix() -> None:
    other = Path(tempfile.mkdtemp(prefix="not_replay_"))
    try:
        with pytest.raises(wt.ReplayWorktreeError):
            wt._assert_temp_root(other)
        assert other.exists()
    finally:
        shutil.rmtree(other, ignore_errors=True)


def test_cleanup_skips_removal_when_guard_rejects(repo: Path, tmp_path: Path) -> None:
    victim = tmp_path / "important"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep\n", encoding="utf-8")

    problems = wt._cleanup(repo, victim / "work", victim)

    assert victim.exists()
    assert (victim / "keep.txt").exists()
    assert any("건너뜀" in problem for problem in problems)
