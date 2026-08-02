"""Issue #0017 replay mock repo 빌더 테스트 — HISTORICAL_REPLAY_SPEC §10, ADR-010.

빌드 결과는 전부 `tmp_path` 아래에 만든다 — 저장소의
`evaluation/fixtures/replay_repos/` 는 어떤 테스트에서도 건드리지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_replay_repos as builder  # noqa: E402

from app.evaluation.replay.loader import REVISION_PATTERN  # noqa: E402

CASE1 = "case1_value_change"
CASE2 = "case2_condition_test"
CASE3 = "case3_unrelated_noise"
ALL_CASES = (CASE1, CASE2, CASE3)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 실행 파일이 없는 환경"
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        check=True,
    )
    return proc.stdout


def _relative_files(root: Path) -> set[str]:
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }


def _tree_snapshot(root: Path) -> dict:
    return {rel: (root / rel).read_text(encoding="utf-8") for rel in _relative_files(root)}


@pytest.fixture(scope="module")
def built_repos(tmp_path_factory) -> Path:
    """저장소의 실제 replay_sources/ 를 tmp 출력 루트에 한 번만 빌드한다."""
    output_root = tmp_path_factory.mktemp("replay_repos")
    builder.build_replay_repos(source_root=builder.SOURCE_ROOT, output_root=output_root)
    return output_root


# ---------------------------------------------------------------------------
# 원본 트리 (커밋 대상)
# ---------------------------------------------------------------------------


def test_replay_sources_에_케이스_3건이_있다():
    assert builder.discover_cases(builder.SOURCE_ROOT) == list(ALL_CASES)


def test_케이스마다_base와_answer_스냅샷이_모두_있다():
    for case in ALL_CASES:
        base = builder.SOURCE_ROOT / case / "base"
        answer = builder.SOURCE_ROOT / case / "answer"
        assert base.is_dir() and answer.is_dir()
        assert _relative_files(base), f"{case}: base 트리가 비어 있다"
        assert _relative_files(answer), f"{case}: answer 트리가 비어 있다"


# ---------------------------------------------------------------------------
# 빌드 결과 — repo·태그
# ---------------------------------------------------------------------------


def test_케이스마다_repo가_생기고_base_answer_태그를_갖는다(built_repos: Path):
    for case in ALL_CASES:
        repo = built_repos / case
        assert (repo / ".git").is_dir(), f"{case}: git repo가 생성되지 않았다"
        tags = sorted(_git(repo, "tag", "-l").split())
        assert tags == [f"{case}/answer", f"{case}/base"]


def test_태그명이_로더의_revision_문자_규칙을_만족한다(built_repos: Path):
    """Step 1 로더가 fixture YAML 의 base_commit/answer_commit 으로 받아들여야 한다."""
    for case in ALL_CASES:
        for tag in _git(built_repos / case, "tag", "-l").split():
            assert REVISION_PATTERN.match(tag), tag
            assert ".." not in tag


def test_base_태그_시점_파일이_replay_sources의_base와_일치한다(built_repos: Path):
    for case in ALL_CASES:
        source_base = builder.SOURCE_ROOT / case / "base"
        repo = built_repos / case
        tracked = set(_git(repo, "ls-tree", "-r", "--name-only", f"{case}/base").split("\n"))
        tracked.discard("")
        assert tracked == _relative_files(source_base)
        for rel in sorted(tracked):
            shown = _git(repo, "show", f"{case}/base:{rel}")
            assert shown == (source_base / rel).read_text(encoding="utf-8")


def test_answer_태그_시점_파일이_replay_sources의_answer와_일치한다(built_repos: Path):
    for case in ALL_CASES:
        source_answer = builder.SOURCE_ROOT / case / "answer"
        repo = built_repos / case
        tracked = set(_git(repo, "ls-tree", "-r", "--name-only", f"{case}/answer").split("\n"))
        tracked.discard("")
        assert tracked == _relative_files(source_answer)
        for rel in sorted(tracked):
            shown = _git(repo, "show", f"{case}/answer:{rel}")
            assert shown == (source_answer / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 케이스별 성질 (SPEC §10)
# ---------------------------------------------------------------------------


def _answer_diff_files(repo: Path, case: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", f"{case}/base", f"{case}/answer")
    return sorted(line for line in out.split("\n") if line)


def test_case1_answer_diff는_단일_value_change다(built_repos: Path):
    changed = _answer_diff_files(built_repos / CASE1, CASE1)
    assert changed == ["src/main/java/com/example/tax/TaxConstants.java"]

    diff = _git(built_repos / CASE1, "diff", f"{CASE1}/base", f"{CASE1}/answer")
    assert "-    public static final long CHILD_TAX_CREDIT = 150000L;" in diff
    assert "+    public static final long CHILD_TAX_CREDIT = 250000L;" in diff


def test_case2_answer_diff는_로직과_테스트_두_파일이다(built_repos: Path):
    changed = _answer_diff_files(built_repos / CASE2, CASE2)
    assert len(changed) == 2, changed
    logic = [p for p in changed if "/src/main/" in f"/{p}"]
    tests = [p for p in changed if "/src/test/" in f"/{p}"]
    assert len(logic) == 1 and len(tests) == 1, changed
    assert logic[0].endswith("AnnualLeaveService.java")
    assert tests[0].endswith("AnnualLeaveServiceTest.java")

    diff = _git(built_repos / CASE2, "diff", f"{CASE2}/base", f"{CASE2}/answer")
    assert "-        if (monthsWorked < 12) {" in diff
    assert "+        if (monthsWorked < 6) {" in diff


def test_case3_answer_diff에_코드와_문서가_함께_들어있다(built_repos: Path):
    """answer commit 전체를 정답으로 보면 안 된다는 것을 검증하기 위한 케이스(SPEC §2·§11)."""
    changed = _answer_diff_files(built_repos / CASE3, CASE3)
    code = [p for p in changed if p.endswith(".java")]
    docs = [p for p in changed if p.endswith(".md")]
    assert len(code) == 1, changed
    assert docs == ["README.md"], changed
    assert len(changed) == len(code) + len(docs)

    diff = _git(built_repos / CASE3, "diff", f"{CASE3}/base", f"{CASE3}/answer")
    assert "MINIMUM_HOURLY_WAGE" in diff


# ---------------------------------------------------------------------------
# 재실행 (idempotency)
# ---------------------------------------------------------------------------


def test_두_번_연속_빌드해도_성공하고_결과가_같다(tmp_path: Path):
    output_root = tmp_path / "out"
    source_root = builder.SOURCE_ROOT

    builder.build_replay_repos(source_root=source_root, output_root=output_root, cases=[CASE1])
    first_sha = _git(output_root / CASE1, "rev-parse", f"{CASE1}/answer").strip()
    first_tree = _tree_snapshot(output_root / CASE1)

    stale = output_root / CASE1 / "stale_leftover.txt"
    stale.write_text("이전 빌드 잔재\n", encoding="utf-8")

    builder.build_replay_repos(source_root=source_root, output_root=output_root, cases=[CASE1])
    second_sha = _git(output_root / CASE1, "rev-parse", f"{CASE1}/answer").strip()

    assert not stale.exists(), "재빌드가 이전 산출물을 지우지 않았다"
    assert second_sha == first_sha
    assert _tree_snapshot(output_root / CASE1) == first_tree
    assert sorted(_git(output_root / CASE1, "tag", "-l").split()) == [
        f"{CASE1}/answer",
        f"{CASE1}/base",
    ]


def test_빌드는_원본_replay_sources를_수정하지_않는다(tmp_path: Path):
    before = _tree_snapshot(builder.SOURCE_ROOT)
    builder.build_replay_repos(source_root=builder.SOURCE_ROOT, output_root=tmp_path / "out")
    assert _tree_snapshot(builder.SOURCE_ROOT) == before


# ---------------------------------------------------------------------------
# 삭제 범위 가드
# ---------------------------------------------------------------------------


def test_출력_루트_밖_경로는_삭제하지_않고_오류로_중단한다(tmp_path: Path):
    output_root = tmp_path / "out"
    output_root.mkdir()
    outsider = tmp_path / "precious"
    outsider.mkdir()
    (outsider / "keep.txt").write_text("건드리면 안 됨\n", encoding="utf-8")

    with pytest.raises(builder.ReplayRepoBuildError, match="출력 루트 밖"):
        builder.reset_case_dir(outsider, output_root)

    assert (outsider / "keep.txt").read_text(encoding="utf-8") == "건드리면 안 됨\n"


def test_상위로_거슬러_올라가는_경로는_거부한다(tmp_path: Path):
    output_root = tmp_path / "out"
    output_root.mkdir()
    with pytest.raises(builder.ReplayRepoBuildError, match="출력 루트 밖"):
        builder.ensure_within(output_root / ".." / "escaped", output_root)


def test_출력_루트_자기_자신도_거부한다(tmp_path: Path):
    output_root = tmp_path / "out"
    output_root.mkdir()
    with pytest.raises(builder.ReplayRepoBuildError, match="출력 루트 밖"):
        builder.ensure_within(output_root, output_root)


def test_루트_밖을_가리키는_심볼릭_링크도_거부한다(tmp_path: Path):
    output_root = tmp_path / "out"
    output_root.mkdir()
    outsider = tmp_path / "precious"
    outsider.mkdir()
    link = output_root / "case_link"
    link.symlink_to(outsider, target_is_directory=True)

    with pytest.raises(builder.ReplayRepoBuildError, match="출력 루트 밖"):
        builder.reset_case_dir(link, output_root)
    assert outsider.is_dir()


def test_케이스_이름은_문자_집합으로_제한된다(tmp_path: Path):
    with pytest.raises(builder.ReplayRepoBuildError, match="허용 형식"):
        builder.build_case("../escape", builder.SOURCE_ROOT, tmp_path)


def test_알_수_없는_케이스는_오류다(tmp_path: Path):
    with pytest.raises(builder.ReplayRepoBuildError, match="알 수 없는 케이스"):
        builder.build_replay_repos(
            source_root=builder.SOURCE_ROOT, output_root=tmp_path, cases=["case9_nope"]
        )


def test_원본_디렉토리가_없으면_오류다(tmp_path: Path):
    with pytest.raises(builder.ReplayRepoBuildError, match="원본 디렉토리가 없습니다"):
        builder.build_replay_repos(source_root=tmp_path / "nope", output_root=tmp_path / "out")


# ---------------------------------------------------------------------------
# git 실패 처리
# ---------------------------------------------------------------------------


def test_git이_없으면_명확한_오류로_중단한다(tmp_path: Path):
    with pytest.raises(builder.ReplayRepoBuildError, match="git 실행 파일을 찾을 수 없습니다"):
        builder.build_replay_repos(
            source_root=builder.SOURCE_ROOT,
            output_root=tmp_path / "out",
            git_bin=str(tmp_path / "git-does-not-exist"),
            cases=[CASE1],
        )


def test_git_명령이_실패하면_오류로_중단한다(tmp_path: Path):
    failing_git = tmp_path / "failing-git"
    failing_git.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n", encoding="utf-8")
    failing_git.chmod(0o755)

    with pytest.raises(builder.ReplayRepoBuildError, match="실패 \\(exit 1\\)"):
        builder.build_replay_repos(
            source_root=builder.SOURCE_ROOT,
            output_root=tmp_path / "out",
            git_bin=str(failing_git),
            cases=[CASE1],
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli는_성공_시_0을_돌려준다(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(builder, "OUTPUT_ROOT", tmp_path / "out")
    assert builder.main(["--case", CASE1]) == 0
    assert "built" in capsys.readouterr().out
    assert (tmp_path / "out" / CASE1 / ".git").is_dir()


def test_cli는_quiet에서_출력하지_않는다(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(builder, "OUTPUT_ROOT", tmp_path / "out")
    assert builder.main(["--quiet", "--case", CASE1]) == 0
    assert capsys.readouterr().out == ""


def test_cli는_git_실패_시_0이_아닌_코드를_돌려준다(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(builder, "OUTPUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(builder, "GIT_BIN", str(tmp_path / "git-does-not-exist"))
    assert builder.main(["--case", CASE1]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_cli는_임의_출력_경로_옵션을_제공하지_않는다(tmp_path, capsys):
    """오타 하나로 사용자 디렉토리가 날아가는 옵션을 만들지 않는다는 계약."""
    for forbidden in ("--output", "--output-root", "--dest", "--target"):
        with pytest.raises(SystemExit):
            builder.main([forbidden, str(tmp_path)])
        capsys.readouterr()


# ---------------------------------------------------------------------------
# 전역 환경 오염 방지
# ---------------------------------------------------------------------------


def test_git_신원은_명령마다_주입되고_전역_설정을_바꾸지_않는다():
    """`git config --global` 을 호출하는 코드가 없어야 한다."""
    source = (PROJECT_ROOT / "scripts" / "build_replay_repos.py").read_text(encoding="utf-8")
    # 산문(주석·docstring)이 아니라 실제 인자로 넘어가는 문자열 리터럴만 본다.
    assert '"--global"' not in source and "'--global'" not in source
    assert "shell=True" not in source
    assert builder.COMMIT_AUTHOR_EMAIL.endswith(".invalid")


def test_상속된_GIT_DIR은_git_환경에서_제거된다(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere/else")
    env = builder._git_env()
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert env["GIT_AUTHOR_NAME"] == builder.COMMIT_AUTHOR_NAME
    assert env["GIT_COMMITTER_EMAIL"] == builder.COMMIT_AUTHOR_EMAIL
    assert os.environ.get("GIT_DIR") == "/somewhere/else/.git", "os.environ 을 직접 건드렸다"
