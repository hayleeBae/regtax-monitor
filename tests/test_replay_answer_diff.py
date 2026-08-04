"""Issue #0018 answer diff 추출 테스트 — HISTORICAL_REPLAY_SPEC §2·§4-9·§11.

실제 git 을 쓰지만 대상은 `tmp_path` 에 빌드한 mock repo 뿐이다 — 저장소의
`evaluation/fixtures/replay_repos/` 나 `REPO_ROOT` 는 건드리지 않는다. 무거운
의존성(임베딩·LLM·ChromaDB)은 import 하지 않는다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_replay_repos as builder  # noqa: E402

from app.evaluation.case import ExpectedReplacement  # noqa: E402
from app.evaluation.replay import answer_diff as ad  # noqa: E402
from app.evaluation.replay.fixture import ReplayScope  # noqa: E402
from app.evaluation.replay.loader import ReplayFixtureLoader  # noqa: E402

CASE1 = "case1_value_change"
CASE2 = "case2_condition_test"
CASE3 = "case3_unrelated_noise"

MOCK_FIXTURE_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "replay" / "mock_cases.yaml"

CASE1_CODE = "src/main/java/com/example/tax/TaxConstants.java"
CASE2_CODE = "src/main/java/com/example/hr/AnnualLeaveService.java"
CASE2_TEST = "src/test/java/com/example/hr/AnnualLeaveServiceTest.java"
CASE3_CODE = "src/main/java/com/example/hr/MinimumWageValidator.java"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 실행 파일이 없는 환경"
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """테스트 준비·관찰용 직접 호출 — 프로덕션 경로가 아니다."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=answer diff test",
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


@pytest.fixture(scope="module")
def built_repos(tmp_path_factory) -> Path:
    """저장소의 replay_sources/ 를 tmp 출력 루트에 한 번만 빌드한다."""
    output_root = tmp_path_factory.mktemp("replay_repos")
    builder.build_replay_repos(source_root=builder.SOURCE_ROOT, output_root=output_root)
    return output_root


@pytest.fixture(scope="module")
def fixtures_by_id() -> dict:
    """mock_cases.yaml 의 실제 fixture — scope 를 테스트가 다시 쓰지 않는다."""
    loaded = ReplayFixtureLoader().load_yaml(MOCK_FIXTURE_PATH)
    return {fixture.case_id: fixture for fixture in loaded}


def _revs(case: str) -> tuple[str, str]:
    return f"{case}/base", f"{case}/answer"


def _paths(changes) -> set:
    return {changed.path for changed in changes}


def _scope(relevant, excluded=(), replacements=()) -> ReplayScope:
    return ReplayScope(
        relevant_paths=tuple(relevant),
        excluded_paths=tuple(excluded),
        expected_replacements=tuple(replacements),
    )


# ---------------------------------------------------------------------------
# 1) case3 — unrelated exclusion (스펙 §11 핵심)
# ---------------------------------------------------------------------------


def test_case3_answer_commit_contains_code_and_doc(built_repos: Path) -> None:
    """전제 확인: answer commit 에 코드와 문서 변경이 함께 들어 있다."""
    repo = built_repos / CASE3
    base, answer = _revs(CASE3)
    diff = ad.extract_answer_diff(repo, base, answer, _scope([]))
    assert _paths(diff.all_changed) == {CASE3_CODE, "README.md"}


def test_case3_excludes_unrelated_doc(built_repos: Path, fixtures_by_id: dict) -> None:
    repo = built_repos / CASE3
    base, answer = _revs(CASE3)
    scope = fixtures_by_id["replay_mock_unrelated_noise"].scope

    diff = ad.extract_answer_diff(repo, base, answer, scope)

    assert _paths(diff.in_scope) == {CASE3_CODE}
    assert _paths(diff.excluded) == {"README.md"}
    assert diff.out_of_scope == ()


def test_case3_without_exclusion_keeps_doc_out_of_scope(built_repos: Path) -> None:
    """excluded_paths 를 비우면 문서는 정답이 아니지만 **버려지지도** 않는다."""
    repo = built_repos / CASE3
    base, answer = _revs(CASE3)

    diff = ad.extract_answer_diff(repo, base, answer, _scope([CASE3_CODE]))

    assert _paths(diff.in_scope) == {CASE3_CODE}
    assert _paths(diff.out_of_scope) == {"README.md"}
    assert diff.excluded == ()


def test_changed_file_line_counts(built_repos: Path, fixtures_by_id: dict) -> None:
    repo = built_repos / CASE3
    base, answer = _revs(CASE3)
    scope = fixtures_by_id["replay_mock_unrelated_noise"].scope

    diff = ad.extract_answer_diff(repo, base, answer, scope)

    (code,) = diff.in_scope
    assert code.status == "M"
    assert (code.added_lines, code.removed_lines) == (1, 1)


# ---------------------------------------------------------------------------
# 2) case2 — relevant_paths 2개
# ---------------------------------------------------------------------------


def test_case2_both_relevant_paths_in_scope(
    built_repos: Path, fixtures_by_id: dict
) -> None:
    repo = built_repos / CASE2
    base, answer = _revs(CASE2)
    scope = fixtures_by_id["replay_mock_condition_test"].scope

    diff = ad.extract_answer_diff(repo, base, answer, scope)

    assert _paths(diff.in_scope) == {CASE2_CODE, CASE2_TEST}
    assert diff.out_of_scope == ()
    assert diff.excluded == ()


# ---------------------------------------------------------------------------
# 3) case1 — 단일 파일
# ---------------------------------------------------------------------------


def test_case1_single_file_in_scope(built_repos: Path, fixtures_by_id: dict) -> None:
    repo = built_repos / CASE1
    base, answer = _revs(CASE1)
    scope = fixtures_by_id["replay_mock_value_change"].scope

    diff = ad.extract_answer_diff(repo, base, answer, scope)

    assert _paths(diff.in_scope) == {CASE1_CODE}
    assert diff.out_of_scope == ()
    assert diff.excluded == ()


def test_answer_diff_partitions_are_disjoint(
    built_repos: Path, fixtures_by_id: dict
) -> None:
    """세 갈래는 겹치지 않고, 합치면 answer commit 의 전체 변경이다."""
    repo = built_repos / CASE3
    base, answer = _revs(CASE3)
    scope = fixtures_by_id["replay_mock_unrelated_noise"].scope

    diff = ad.extract_answer_diff(repo, base, answer, scope)

    buckets = [_paths(diff.in_scope), _paths(diff.out_of_scope), _paths(diff.excluded)]
    total = sum(len(bucket) for bucket in buckets)
    assert len(set().union(*buckets)) == total
    assert len(diff.all_changed) == total


# ---------------------------------------------------------------------------
# 4) 매칭 규칙 (함수 단위)
# ---------------------------------------------------------------------------


def _changed(path: str) -> ad.ChangedFile:
    return ad.ChangedFile(path=path, status="M", added_lines=1, removed_lines=1)


def test_excluded_wins_over_relevant() -> None:
    """같은 경로가 양쪽에 있으면 excluded 다 (로더는 막지만 함수는 우선순위를 지킨다)."""
    scope = _scope(relevant=["src/a.java"], excluded=["src/a.java"])
    diff = ad.classify_changes([_changed("src/a.java")], scope)

    assert diff.excluded == (_changed("src/a.java"),)
    assert diff.in_scope == ()


def test_excluded_subdirectory_wins_over_relevant_directory() -> None:
    scope = _scope(relevant=["src/"], excluded=["src/docs/"])
    diff = ad.classify_changes(
        [_changed("src/main/A.java"), _changed("src/docs/guide.md")], scope
    )

    assert _paths(diff.in_scope) == {"src/main/A.java"}
    assert _paths(diff.excluded) == {"src/docs/guide.md"}


def test_directory_prefix_matching(built_repos: Path) -> None:
    repo = built_repos / CASE2
    base, answer = _revs(CASE2)

    diff = ad.extract_answer_diff(repo, base, answer, _scope(["src/main/"]))

    assert _paths(diff.in_scope) == {CASE2_CODE}
    assert _paths(diff.out_of_scope) == {CASE2_TEST}


def test_directory_prefix_without_trailing_slash() -> None:
    diff = ad.classify_changes([_changed("src/main/A.java")], _scope(["src/main"]))
    assert _paths(diff.in_scope) == {"src/main/A.java"}


def test_prefix_matching_respects_separator_boundary() -> None:
    """`src/main` 이 `src/main2/` 를 삼키지 않는다."""
    diff = ad.classify_changes([_changed("src/main2/A.java")], _scope(["src/main"]))
    assert _paths(diff.out_of_scope) == {"src/main2/A.java"}


def test_windows_separator_is_normalized() -> None:
    diff = ad.classify_changes(
        [_changed("src/main/A.java")], _scope(["src\\main\\A.java"])
    )
    assert _paths(diff.in_scope) == {"src/main/A.java"}


def test_dot_prefix_is_normalized() -> None:
    assert ad.normalize_path("./src//main/A.java") == "src/main/A.java"
    assert ad.path_matches("./src/main/A.java", "src/main/A.java")


def test_empty_scope_leaves_everything_out_of_scope() -> None:
    diff = ad.classify_changes([_changed("src/a.java")], _scope([]))
    assert _paths(diff.out_of_scope) == {"src/a.java"}
    assert diff.in_scope == ()


# ---------------------------------------------------------------------------
# 5) git 출력 파싱
# ---------------------------------------------------------------------------


def test_parse_name_status_handles_rename() -> None:
    output = "R083\0old.txt\0new.txt\0M\0keep.java\0"
    assert ad.parse_name_status(output) == [("R083", "new.txt"), ("M", "keep.java")]


def test_parse_numstat_handles_rename() -> None:
    output = "1\t1\t\0old.txt\0new.txt\0" + "7\t1\tREADME.md\0"
    assert ad.parse_numstat(output) == {"new.txt": (1, 1), "README.md": (7, 1)}


def test_parse_numstat_treats_binary_as_zero() -> None:
    assert ad.parse_numstat("-\t-\tlogo.png\0") == {"logo.png": (0, 0)}


def test_parse_name_status_rejects_truncated_output() -> None:
    with pytest.raises(ad.AnswerDiffError):
        ad.parse_name_status("R100\0only-one-path\0")


def test_rename_uses_new_path(tmp_path: Path) -> None:
    """rename 은 신규 경로로 판정하고 status 에 원본 코드를 남긴다."""
    repo = tmp_path / "rename_repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    old = repo / "Old.java"
    old.write_text("class A { long LIMIT = 150000L; }\n" * 5, encoding="utf-8")
    _git(repo, "add", "--all", "--force", "--", ".")
    _git(repo, "commit", "--no-verify", "-m", "base")
    _git(repo, "tag", "case/base")
    _git(repo, "mv", "Old.java", "New.java")
    (repo / "New.java").write_text(
        "class A { long LIMIT = 250000L; }\n" + "class A { long LIMIT = 150000L; }\n" * 4,
        encoding="utf-8",
    )
    _git(repo, "add", "--all", "--force", "--", ".")
    _git(repo, "commit", "--no-verify", "-m", "answer")
    _git(repo, "tag", "case/answer")

    diff = ad.extract_answer_diff(repo, "case/base", "case/answer", _scope(["New.java"]))

    assert _paths(diff.in_scope) == {"New.java"}
    (changed,) = diff.in_scope
    assert changed.status.startswith("R")


# ---------------------------------------------------------------------------
# 6) expected_replacements 대조 (fixture 검증)
# ---------------------------------------------------------------------------


def test_expected_replacements_match_answer_commit(
    built_repos: Path, fixtures_by_id: dict
) -> None:
    repo = built_repos / CASE2
    _, answer = _revs(CASE2)
    scope = fixtures_by_id["replay_mock_condition_test"].scope

    checks = ad.check_expected_replacements(repo, answer, scope)

    assert len(checks) == 2
    for check in checks:
        assert check.path_exists is True
        assert check.found_after is True
        assert check.found_before is False
        assert check.ok is True


def test_expected_replacement_wrong_after_is_reported(built_repos: Path) -> None:
    repo = built_repos / CASE1
    _, answer = _revs(CASE1)
    scope = _scope(
        [CASE1_CODE],
        replacements=[
            ExpectedReplacement(path=CASE1_CODE, before="150000L", after="999999L")
        ],
    )

    (check,) = ad.check_expected_replacements(repo, answer, scope)

    assert check.path_exists is True
    assert check.found_after is False
    assert check.ok is False


def test_expected_replacement_before_still_present_is_reported(
    built_repos: Path,
) -> None:
    """answer 시점에 before 가 남아 있으면 fixture 가 어긋난 것이다."""
    repo = built_repos / CASE1
    _, answer = _revs(CASE1)
    scope = _scope(
        [CASE1_CODE],
        replacements=[
            # 개정 후에도 남아 있는 문자열을 before 로 잘못 지정한 fixture
            ExpectedReplacement(path=CASE1_CODE, before="public", after="250000L")
        ],
    )

    (check,) = ad.check_expected_replacements(repo, answer, scope)

    assert check.found_after is True
    assert check.found_before is True
    assert check.ok is False


def test_expected_replacement_missing_path_is_reported_not_raised(
    built_repos: Path,
) -> None:
    repo = built_repos / CASE1
    _, answer = _revs(CASE1)
    scope = _scope(
        [CASE1_CODE],
        replacements=[
            ExpectedReplacement(path="src/main/java/Nope.java", before="a", after="b")
        ],
    )

    (check,) = ad.check_expected_replacements(repo, answer, scope)

    assert check.path_exists is False
    assert check.found_after is False
    assert check.found_before is False
    assert check.ok is False


def test_expected_replacement_normalized_text_mode(built_repos: Path) -> None:
    """`normalized_text` 는 공백 차이를 무시한다."""
    repo = built_repos / CASE2
    _, answer = _revs(CASE2)
    spaced = ReplayScope(
        relevant_paths=(CASE2_CODE,),
        expected_replacements=(
            ExpectedReplacement(
                path=CASE2_CODE,
                before="monthsWorked   <   12",
                after="monthsWorked   <   6",
                match_mode="normalized_text",
            ),
        ),
    )
    exact = ReplayScope(
        relevant_paths=(CASE2_CODE,),
        expected_replacements=(
            ExpectedReplacement(
                path=CASE2_CODE,
                before="monthsWorked   <   12",
                after="monthsWorked   <   6",
                match_mode="exact",
            ),
        ),
    )

    (normalized_check,) = ad.check_expected_replacements(repo, answer, spaced)
    (exact_check,) = ad.check_expected_replacements(repo, answer, exact)

    assert normalized_check.found_after is True
    assert normalized_check.found_before is False
    assert exact_check.found_after is False


def test_empty_needle_never_matches(built_repos: Path) -> None:
    """빈 문자열은 어디에나 있으므로 fixture 오류를 통과시키면 안 된다."""
    repo = built_repos / CASE1
    _, answer = _revs(CASE1)
    scope = _scope(
        [CASE1_CODE],
        replacements=[ExpectedReplacement(path=CASE1_CODE, before="", after="")],
    )

    (check,) = ad.check_expected_replacements(repo, answer, scope)

    assert check.path_exists is True
    assert check.found_after is False


def test_check_expected_replacements_empty_scope(built_repos: Path) -> None:
    repo = built_repos / CASE1
    _, answer = _revs(CASE1)
    assert ad.check_expected_replacements(repo, answer, _scope([CASE1_CODE])) == ()


# ---------------------------------------------------------------------------
# 7) 원본 무변경 (스펙 §2·§12)
# ---------------------------------------------------------------------------


def test_original_repo_unchanged(built_repos: Path, fixtures_by_id: dict) -> None:
    repo = built_repos / CASE3
    base, answer = _revs(CASE3)
    scope = fixtures_by_id["replay_mock_unrelated_noise"].scope

    before = (
        _git(repo, "status", "--porcelain"),
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "worktree", "list"),
    )

    ad.extract_answer_diff(repo, base, answer, scope)
    ad.check_expected_replacements(repo, answer, scope)

    after = (
        _git(repo, "status", "--porcelain"),
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "worktree", "list"),
    )
    assert before == after
    assert before[0] == ""


def test_missing_commit_raises_answer_diff_error(built_repos: Path) -> None:
    repo = built_repos / CASE1
    with pytest.raises(ad.AnswerDiffError):
        ad.extract_answer_diff(repo, "no-such-rev", "case1_value_change/answer", _scope([]))


def test_error_message_does_not_leak_repo_path(built_repos: Path) -> None:
    """오류 메시지에 repo 절대경로가 들어가면 회사 경로가 리포트로 샌다."""
    repo = built_repos / CASE1
    with pytest.raises(ad.AnswerDiffError) as excinfo:
        ad.extract_answer_diff(repo, "no-such-rev", "case1_value_change/answer", _scope([]))
    assert str(repo) not in str(excinfo.value)


def test_module_does_not_call_subprocess_directly() -> None:
    """git 은 반드시 `git_cmd` wrapper 를 거친다 (ARCHITECTURE 레이어 규칙)."""
    source = (
        PROJECT_ROOT / "app" / "evaluation" / "replay" / "answer_diff.py"
    ).read_text(encoding="utf-8")
    assert "subprocess" not in source
