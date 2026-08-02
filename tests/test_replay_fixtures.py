"""Issue #0017 replay fixture 데이터 테스트 — HISTORICAL_REPLAY_SPEC §3·§8·§10.

Step 3 이 추가한 **데이터 파일**(mock fixture 3건 + 회사 템플릿)이 Step 1 로더의
검증을 그대로 통과하는지, 그리고 fixture 가 가리키는 태그가 Step 2 빌드 결과에
실제로 존재하는지 고정한다. 로더를 느슨하게 고쳐 통과시키는 일을 막기 위해 검증
규칙은 건드리지 않고 fixture 쪽만 맞춘다.

git repo 검사는 `evaluation/fixtures/replay_repos/` 가 이미 빌드되어 있을 때만
수행한다(gitignore 대상이라 clone 직후에는 없다). 무거운 의존성(임베딩·Chroma·LLM)은
어느 테스트에서도 건드리지 않는다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.evaluation.replay.fixture import PrivacyMode
from app.evaluation.replay.loader import ReplayFixtureLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MOCK_FIXTURE_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "replay" / "mock_cases.yaml"
COMPANY_TEMPLATE_PATH = (
    PROJECT_ROOT / "evaluation" / "datasets" / "company_replay.template.yaml"
)

CASE_VALUE_CHANGE = "replay_mock_value_change"
CASE_CONDITION_TEST = "replay_mock_condition_test"
CASE_UNRELATED_NOISE = "replay_mock_unrelated_noise"

SHA40_PATTERN = re.compile(r"\b[0-9a-fA-F]{40}\b")
"""템플릿에 실제 commit SHA 가 섞여 들어갔는지 찾는 패턴."""


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_fixtures():
    return ReplayFixtureLoader().load_yaml(MOCK_FIXTURE_PATH)


@pytest.fixture(scope="module")
def by_id(mock_fixtures):
    return {fixture.case_id: fixture for fixture in mock_fixtures}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        check=False,
    )


# ---------------------------------------------------------------------------
# mock fixture 3건 (SPEC §10)
# ---------------------------------------------------------------------------


def test_mock_fixture_file_exists():
    assert MOCK_FIXTURE_PATH.exists()


def test_mock_fixtures_load_without_error(mock_fixtures):
    """Step 1 로더 검증(경로·revision·golden_command·traversal)을 전부 통과한다."""
    assert len(mock_fixtures) == 3


def test_mock_case_ids(by_id):
    assert set(by_id) == {
        CASE_VALUE_CHANGE,
        CASE_CONDITION_TEST,
        CASE_UNRELATED_NOISE,
    }


def test_mock_fixtures_use_relative_path_not_env(mock_fixtures):
    """mock 은 path 를 쓴다 — path_env 면 환경변수 없이는 #0018 이 실행할 수 없다."""
    for fixture in mock_fixtures:
        assert fixture.repository.path is not None, fixture.case_id
        assert fixture.repository.path_env is None, fixture.case_id
        assert not Path(fixture.repository.path).is_absolute()
        assert fixture.repository.source_type == "local_git"


def test_mock_fixtures_are_full_privacy(mock_fixtures):
    """합성 데이터이므로 full 이 적절하다(SPEC §8)."""
    for fixture in mock_fixtures:
        assert fixture.execution.privacy_mode is PrivacyMode.FULL, fixture.case_id


def test_mock_fixtures_have_no_golden_command(mock_fixtures):
    """mock repo 에는 빌드 도구가 없다."""
    for fixture in mock_fixtures:
        assert fixture.execution.golden_command is None, fixture.case_id


def test_mock_fixtures_are_reviewed(mock_fixtures):
    for fixture in mock_fixtures:
        assert fixture.reviewed is True, fixture.case_id


def test_mock_commits_are_tags_not_shas(mock_fixtures):
    """SHA 는 빌드마다 달라진다 — 태그 이름을 쓴다(ADR-010)."""
    for fixture in mock_fixtures:
        for revision in (
            fixture.repository.base_commit,
            fixture.repository.answer_commit,
        ):
            assert revision.endswith(("/base", "/answer")), revision
            assert not SHA40_PATTERN.search(revision), revision


def test_condition_case_covers_logic_and_test_files(by_id):
    """조건 개정은 로직과 테스트를 함께 고쳐야 정답이다(SPEC §10-2)."""
    scope = by_id[CASE_CONDITION_TEST].scope
    assert len(scope.relevant_paths) >= 2
    assert any(path.endswith("AnnualLeaveService.java") for path in scope.relevant_paths)
    assert any(
        path.endswith("AnnualLeaveServiceTest.java") for path in scope.relevant_paths
    )


def test_unrelated_noise_case_excludes_document(by_id):
    """answer commit 전체를 정답으로 쓰지 않는다(SPEC §2·§13)."""
    scope = by_id[CASE_UNRELATED_NOISE].scope
    assert scope.excluded_paths
    assert any(path.endswith(".md") for path in scope.excluded_paths)
    assert not any(path.endswith(".md") for path in scope.relevant_paths)


def test_every_mock_case_declares_expected_replacements(mock_fixtures):
    for fixture in mock_fixtures:
        assert fixture.scope.expected_replacements, fixture.case_id
        for replacement in fixture.scope.expected_replacements:
            assert replacement.path in fixture.scope.relevant_paths, fixture.case_id
            assert replacement.before != replacement.after


# ---------------------------------------------------------------------------
# 빌드된 repo 와의 정합성 (Step 2 결과가 있을 때만)
# ---------------------------------------------------------------------------


def _repo_dir(fixture) -> Path:
    assert fixture.repository.path is not None
    return PROJECT_ROOT / fixture.repository.path


def _require_built_repo(fixture) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git 실행 파일이 없는 환경")
    repo = _repo_dir(fixture)
    if not (repo / ".git").is_dir():
        pytest.skip(
            f"replay repo 미빌드({repo.name}) — python3 scripts/build_replay_repos.py"
        )
    return repo


@pytest.mark.parametrize(
    "case_id", [CASE_VALUE_CHANGE, CASE_CONDITION_TEST, CASE_UNRELATED_NOISE]
)
def test_fixture_tags_exist_in_built_repo(by_id, case_id):
    fixture = by_id[case_id]
    repo = _require_built_repo(fixture)
    for revision in (
        fixture.repository.base_commit,
        fixture.repository.answer_commit,
    ):
        proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
        assert proc.returncode == 0, f"{case_id}: 태그 없음 {revision!r}\n{proc.stderr}"


@pytest.mark.parametrize(
    "case_id", [CASE_VALUE_CHANGE, CASE_CONDITION_TEST, CASE_UNRELATED_NOISE]
)
def test_relevant_paths_exist_at_answer_commit(by_id, case_id):
    fixture = by_id[case_id]
    repo = _require_built_repo(fixture)
    answer = fixture.repository.answer_commit
    for path in fixture.scope.relevant_paths:
        proc = _git(repo, "cat-file", "-e", f"{answer}:{path}")
        assert proc.returncode == 0, f"{case_id}: answer 에 없는 경로 {path!r}"


@pytest.mark.parametrize(
    "case_id", [CASE_VALUE_CHANGE, CASE_CONDITION_TEST, CASE_UNRELATED_NOISE]
)
def test_expected_replacements_match_base_and_answer(by_id, case_id):
    """before 는 base 에, after 는 answer 에 실제로 존재한다."""
    fixture = by_id[case_id]
    repo = _require_built_repo(fixture)
    base = fixture.repository.base_commit
    answer = fixture.repository.answer_commit
    for replacement in fixture.scope.expected_replacements:
        base_text = _git(repo, "show", f"{base}:{replacement.path}").stdout
        answer_text = _git(repo, "show", f"{answer}:{replacement.path}").stdout
        assert replacement.before in base_text, f"{case_id}: {replacement.before!r}"
        assert replacement.after in answer_text, f"{case_id}: {replacement.after!r}"


def test_unrelated_document_actually_changed_in_answer(by_id):
    """case3 의 배제 대상이 answer commit 에 실제로 섞여 있어야 케이스가 의미를 갖는다."""
    fixture = by_id[CASE_UNRELATED_NOISE]
    repo = _require_built_repo(fixture)
    proc = _git(
        repo,
        "diff",
        "--name-only",
        fixture.repository.base_commit,
        fixture.repository.answer_commit,
    )
    changed = set(proc.stdout.split())
    assert set(fixture.scope.excluded_paths) & changed
    assert set(fixture.scope.relevant_paths) <= changed


# ---------------------------------------------------------------------------
# 회사 실데이터 템플릿 (SPEC §8, CLAUDE.md 코드 반출 금지)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def company_fixtures():
    return ReplayFixtureLoader().load_yaml(COMPANY_TEMPLATE_PATH)


def test_company_template_loads(company_fixtures):
    """placeholder 값 그대로도 로더 검증을 통과해야 한다 — 통과 못 하면 템플릿을 고친다."""
    assert len(company_fixtures) == 1


def test_company_template_uses_path_env(company_fixtures):
    repository = company_fixtures[0].repository
    assert repository.path is None
    assert repository.path_env == "EHR_REPO_ROOT"


def test_company_template_defaults_to_metadata_only(company_fixtures):
    assert company_fixtures[0].execution.privacy_mode is PrivacyMode.METADATA_ONLY


def test_company_template_has_no_real_sha():
    """실수로 실제 commit SHA 를 넣고 커밋하는 것을 막는 회귀 테스트."""
    text = COMPANY_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert not SHA40_PATTERN.search(text)


def test_company_template_has_no_absolute_path():
    """실제 회사 repo 절대경로가 템플릿에 남으면 파일 자체가 반출 위험물이 된다."""
    text = COMPANY_TEMPLATE_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("/"), line
        value = stripped.split(":", 1)[-1].strip().strip("\"'")
        assert not value.startswith("/"), line
        assert not value.startswith("~"), line


def test_company_template_placeholders_are_not_committed_as_private_data():
    """템플릿은 커밋 대상이지만, 채워 넣는 자리는 evaluation/private/ 임을 명시한다."""
    text = COMPANY_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "evaluation/private/" in text
    assert "커밋하지 않는다" in text


def test_private_directory_has_no_committed_fixture():
    """evaluation/private/ 는 회사 환경에서 사용자가 채우는 자리다(gitignore)."""
    tracked = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "evaluation/private"],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        check=False,
    )
    if tracked.returncode != 0:
        pytest.skip("git 저장소가 아닌 환경")
    assert tracked.stdout.strip() == ""
