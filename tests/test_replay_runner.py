"""Issue #0018 replay runner 테스트 — HISTORICAL_REPLAY_SPEC §4·§8·§9·§12.

실제 git 을 쓰지만 대상은 `tmp_path` 에 빌드한 mock repo 뿐이다 — 저장소의
`evaluation/fixtures/replay_repos/` 나 `REPO_ROOT` 는 건드리지 않는다. 파이프라인은
결정적 stub 만 쓴다(CLAUDE.md — 테스트에서 임베딩·벡터DB·추론 백엔드 금지).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_replay_repos as builder  # noqa: E402

from app.evaluation.replay import runner as rn  # noqa: E402
from app.evaluation.replay import stub_pipeline as stubs  # noqa: E402
from app.evaluation.replay.fixture import PrivacyMode  # noqa: E402
from app.evaluation.replay.loader import ReplayFixtureLoader  # noqa: E402

CASE1 = "replay_mock_value_change"
CASE2 = "replay_mock_condition_test"
CASE3 = "replay_mock_unrelated_noise"

REPO_DIRS = ("case1_value_change", "case2_condition_test", "case3_unrelated_noise")

MOCK_FIXTURE_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "replay" / "mock_cases.yaml"

CASE1_CODE = "src/main/java/com/example/tax/TaxConstants.java"
CASE3_CODE = "src/main/java/com/example/hr/MinimumWageValidator.java"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 실행 파일이 없는 환경"
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """관찰용 직접 호출 — 프로덕션 경로가 아니다(runner 는 git_cmd 만 쓴다)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        check=True,
    )
    return proc.stdout


def _repo_state(repo: Path) -> tuple:
    """원본 repo 가 변하지 않았음을 보는 세 가지 관측값 (스펙 §2·§12)."""
    return (
        _git(repo, "status", "--porcelain"),
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "worktree", "list"),
    )


@pytest.fixture(scope="module")
def project_root(tmp_path_factory) -> Path:
    """mock repo 3개를 tmp 프로젝트 루트 아래 fixture 경로에 빌드한다.

    `repository.path` 가 프로젝트 상대 경로이므로, 빌드 위치를 그 상대 경로에 맞춰
    두면 저장소의 실제 `replay_repos/` 를 쓰지 않고도 같은 fixture 를 그대로 쓸 수 있다.
    """
    root = tmp_path_factory.mktemp("replay_project")
    builder.build_replay_repos(
        source_root=builder.SOURCE_ROOT,
        output_root=root / "evaluation" / "fixtures" / "replay_repos",
    )
    return root


@pytest.fixture(scope="module")
def fixtures() -> tuple:
    return tuple(ReplayFixtureLoader().load_yaml(MOCK_FIXTURE_PATH))


@pytest.fixture
def isolated_tmpdir(monkeypatch, tmp_path) -> Path:
    """임시 디렉토리를 테스트 전용 위치로 돌린다 — 누수 여부를 정확히 셀 수 있다.

    `tempfile.tempdir` 을 바꾸면 `mkdtemp`/`mkstemp` 와 worktree 모듈의 삭제 가드
    (`gettempdir()` 하위 확인)가 같은 루트를 본다.
    """
    root = tmp_path / "tmproot"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _repo_path(project_root: Path, name: str) -> Path:
    return project_root / "evaluation" / "fixtures" / "replay_repos" / name


def _read_report(output_dir: Path) -> dict:
    return json.loads((output_dir / "replay_report.json").read_text(encoding="utf-8"))


def _cases(report: dict) -> dict:
    return {case["case_id"]: case for case in report["cases"]}


def _run(project_root: Path, fixtures, pipeline, output_dir: Path, **kwargs) -> dict:
    rn.run_fixtures(fixtures, pipeline, project_root, output_dir, **kwargs)
    return _read_report(output_dir)


@pytest.fixture(scope="module")
def perfect_report(project_root, fixtures, tmp_path_factory) -> dict:
    output_dir = tmp_path_factory.mktemp("perfect")
    return _run(
        project_root, fixtures, stubs.perfect_pipeline(fixtures), output_dir
    )


@pytest.fixture(scope="module")
def partial_report(project_root, fixtures, tmp_path_factory) -> dict:
    output_dir = tmp_path_factory.mktemp("partial")
    return _run(
        project_root, fixtures, stubs.partial_pipeline(fixtures), output_dir
    )


@pytest.fixture(scope="module")
def empty_report(project_root, fixtures, tmp_path_factory) -> dict:
    output_dir = tmp_path_factory.mktemp("empty")
    return _run(project_root, fixtures, stubs.empty_pipeline(fixtures), output_dir)


# ---------------------------------------------------------------------------
# fixture 3개 실행 (수용 기준)
# ---------------------------------------------------------------------------


def test_three_fixtures_are_executed(perfect_report):
    assert perfect_report["summary"]["case_count"] == 3
    assert set(_cases(perfect_report)) == {CASE1, CASE2, CASE3}


def test_report_files_are_written(project_root, fixtures, tmp_path):
    output_dir = tmp_path / "out"
    target = rn.run_fixtures(
        fixtures, stubs.perfect_pipeline(fixtures), project_root, output_dir
    )
    assert target == output_dir
    for name in ("replay_report.json", "replay_report.md", "environment.json"):
        assert (output_dir / name).is_file()


def test_environment_records_runner_version(project_root, fixtures, tmp_path):
    output_dir = tmp_path / "out"
    rn.run_fixtures(
        fixtures, stubs.perfect_pipeline(fixtures), project_root, output_dir
    )
    environment = json.loads((output_dir / "environment.json").read_text(encoding="utf-8"))
    assert environment["replay_runner_version"] == rn.REPLAY_RUNNER_VERSION
    assert environment["case_count"] == 3
    assert environment["privacy_mode_source"] == "fixture_strictest"


def test_perfect_stub_passes_every_case(perfect_report):
    for case in perfect_report["cases"]:
        assert case["passed"] is True, case["case_id"]
        assert case["failure_kind"] is None
        assert case["git_apply"] is True
        # mock fixture 에는 golden_command 가 없다 — 건너뛴 것이 실패는 아니다.
        assert case["golden_result"] == "skipped"


# ---------------------------------------------------------------------------
# 원본 무변경 (스펙 §2·§12)
# ---------------------------------------------------------------------------


def test_original_repos_are_unchanged(project_root, fixtures, tmp_path):
    before = {name: _repo_state(_repo_path(project_root, name)) for name in REPO_DIRS}

    rn.run_fixtures(
        fixtures, stubs.perfect_pipeline(fixtures), project_root, tmp_path / "out"
    )

    after = {name: _repo_state(_repo_path(project_root, name)) for name in REPO_DIRS}
    assert after == before


def test_generated_patch_is_not_applied_to_the_original_repo(
    project_root, fixtures, tmp_path
):
    repo = _repo_path(project_root, "case1_value_change")
    source = repo / CASE1_CODE
    original_text = source.read_text(encoding="utf-8")

    rn.run_fixtures(
        fixtures, stubs.perfect_pipeline(fixtures), project_root, tmp_path / "out"
    )

    # 초안은 150000L → 250000L 을 만들지만 그 적용은 임시 worktree 안에서만 일어난다.
    assert source.read_text(encoding="utf-8") == original_text
    assert _git(repo, "status", "--porcelain") == ""


def test_no_worktree_is_left_behind(project_root, fixtures, tmp_path):
    repo = _repo_path(project_root, "case3_unrelated_noise")
    before = _git(repo, "worktree", "list")

    rn.run_fixtures(
        fixtures, stubs.perfect_pipeline(fixtures), project_root, tmp_path / "out"
    )

    assert _git(repo, "worktree", "list") == before
    assert not (repo / ".git" / "worktrees").exists()


# ---------------------------------------------------------------------------
# 실패 격리와 임시 디렉토리 정리 (스펙 §9·§12)
# ---------------------------------------------------------------------------


def _exploding(case_id: str, base):
    def pipeline(context):
        if context.case_id == case_id:
            raise RuntimeError("stub pipeline failure")
        return base(context)

    return pipeline


def test_pipeline_failure_does_not_stop_other_cases(project_root, fixtures, tmp_path):
    pipeline = _exploding(CASE2, stubs.perfect_pipeline(fixtures))

    report = _run(project_root, fixtures, pipeline, tmp_path / "out")

    cases = _cases(report)
    assert cases[CASE2]["failure_kind"] == rn.FAILURE_PIPELINE
    assert cases[CASE2]["passed"] is False
    # 나머지 두 건은 그대로 끝까지 실행된다.
    assert cases[CASE1]["passed"] is True
    assert cases[CASE3]["passed"] is True
    assert report["summary"]["case_count"] == 3


def test_failed_case_leaves_no_temp_directory(
    project_root, fixtures, tmp_path, isolated_tmpdir
):
    pipeline = _exploding(CASE2, stubs.perfect_pipeline(fixtures))

    rn.run_fixtures(fixtures, pipeline, project_root, tmp_path / "out")

    # worktree 임시 root 도, patch 임시 파일도 남지 않는다.
    assert list(isolated_tmpdir.iterdir()) == []
    for name in REPO_DIRS:
        repo = _repo_path(project_root, name)
        assert _git(repo, "status", "--porcelain") == ""


def test_successful_run_leaves_no_temp_directory(
    project_root, fixtures, tmp_path, isolated_tmpdir
):
    rn.run_fixtures(
        fixtures, stubs.perfect_pipeline(fixtures), project_root, tmp_path / "out"
    )

    assert list(isolated_tmpdir.iterdir()) == []


def test_missing_commit_is_reported_as_a_case_failure(project_root, fixtures, tmp_path):
    broken = dataclasses.replace(
        fixtures[0],
        repository=dataclasses.replace(
            fixtures[0].repository, base_commit="no_such_revision"
        ),
    )

    report = _run(
        project_root,
        (broken, fixtures[1]),
        stubs.perfect_pipeline(fixtures),
        tmp_path / "out",
    )

    cases = _cases(report)
    assert cases[CASE1]["failure_kind"] == rn.FAILURE_COMMIT
    assert cases[CASE1]["passed"] is False
    assert cases[CASE2]["passed"] is True


def test_missing_repository_is_reported_as_a_case_failure(fixtures, tmp_path):
    # repo 가 없는 프로젝트 루트를 주면 첫 단계에서 멈춘다 — worktree 를 만들지 않는다.
    report = _run(
        tmp_path / "empty_project",
        fixtures[:1],
        stubs.perfect_pipeline(fixtures),
        tmp_path / "out",
    )

    case = _cases(report)[CASE1]
    assert case["failure_kind"] == rn.FAILURE_REPO
    assert case["passed"] is False


def test_failure_kind_is_a_fixed_vocabulary(project_root, fixtures, tmp_path):
    """실패 코드에 예외 메시지·경로가 섞이지 않는다(리포트로 새면 반출이다)."""
    known = {
        rn.FAILURE_REPO,
        rn.FAILURE_COMMIT,
        rn.FAILURE_DIRTY,
        rn.FAILURE_WORKTREE,
        rn.FAILURE_PIPELINE,
        rn.FAILURE_ANSWER_DIFF,
        rn.FAILURE_CLEANUP,
    }
    pipeline = _exploding(CASE2, stubs.perfect_pipeline(fixtures))

    report = _run(project_root, fixtures, pipeline, tmp_path / "out")

    for case in report["cases"]:
        assert case["failure_kind"] is None or case["failure_kind"] in known


# ---------------------------------------------------------------------------
# file coverage / replacement accuracy 보고 (수용 기준)
# ---------------------------------------------------------------------------


def _metrics(report: dict, case_id: str) -> dict:
    return _cases(report)[case_id]["metrics"]


def test_perfect_stub_metrics_are_full_marks(perfect_report):
    for case_id in (CASE1, CASE2, CASE3):
        metrics = _metrics(perfect_report, case_id)
        assert metrics["expected_replacement_accuracy"] == 1.0
        assert metrics["file_coverage"] == 1.0
        assert metrics["unnecessary_file_rate"] == 0.0
        assert metrics["changed_file_jaccard"] == 1.0
        assert metrics["relevant_path_recall_at_k"]["5"] == 1.0
        assert metrics["primary_rank"] == 1


def test_partial_stub_lowers_accuracy_and_coverage(partial_report):
    # case2 는 기대 교체가 2건(로직+테스트)이고 stub 은 첫 건만 수행한다.
    metrics = _metrics(partial_report, CASE2)
    assert metrics["expected_replacement_accuracy"] == 0.5
    assert metrics["file_coverage"] == 0.5
    assert metrics["changed_file_jaccard"] == 0.5
    assert _cases(partial_report)[CASE2]["passed"] is False

    # 기대 교체가 1건인 케이스는 perfect 와 같다 — 차이의 원인이 분명해야 한다.
    assert _metrics(partial_report, CASE1)["expected_replacement_accuracy"] == 1.0
    assert _cases(partial_report)[CASE1]["passed"] is True


def test_empty_stub_scores_zero(empty_report):
    for case_id in (CASE1, CASE2, CASE3):
        case = _cases(empty_report)[case_id]
        metrics = case["metrics"]
        assert metrics["expected_replacement_accuracy"] == 0.0
        assert metrics["file_coverage"] == 0.0
        assert metrics["changed_file_jaccard"] == 0.0
        assert metrics["relevant_path_recall_at_k"]["10"] == 0.0
        assert metrics["primary_rank"] is None
        # 빈 diff 는 "적용하지 않음"이지 apply 실패가 아니다.
        assert case["git_apply"] is None
        assert case["passed"] is False


def test_metrics_move_in_the_expected_direction(
    perfect_report, partial_report, empty_report
):
    def mean(report: dict, key: str) -> float:
        return report["summary"][key]

    for key in ("expected_replacement_accuracy", "file_coverage"):
        assert mean(perfect_report, key) > mean(partial_report, key)
        assert mean(partial_report, key) > mean(empty_report, key)
    assert mean(empty_report, "pass_rate") == 0.0
    assert mean(perfect_report, "pass_rate") == 1.0


# ---------------------------------------------------------------------------
# answer commit 전체 일치를 성공 기준으로 삼지 않는다 (수용 기준)
# ---------------------------------------------------------------------------


def test_untouched_excluded_doc_does_not_lower_coverage(perfect_report):
    """case3 의 answer commit 에는 README 변경이 섞여 있다 (스펙 §10-3).

    초안은 문서를 건드리지 않았지만 README 는 excluded 이므로 정답이 아니다 —
    coverage 는 1.0 이고 케이스는 합격이다.
    """
    case = _cases(perfect_report)[CASE3]
    counts = case["counts"]

    assert counts["answer_excluded_files"] == 1  # README.md
    assert counts["answer_in_scope_files"] == 1
    assert counts["generated_files"] == 1
    assert case["metrics"]["file_coverage"] == 1.0
    assert case["metrics"]["unnecessary_file_rate"] == 0.0
    assert case["passed"] is True


def test_generated_files_are_only_the_touched_ones(perfect_report):
    case = _cases(perfect_report)[CASE3]
    paths = [entry["path"] for entry in case["generated_files"]]
    assert paths == [CASE3_CODE]


# ---------------------------------------------------------------------------
# privacy (스펙 §8)
# ---------------------------------------------------------------------------


def _with_mode(fixture, mode: PrivacyMode):
    return dataclasses.replace(
        fixture, execution=dataclasses.replace(fixture.execution, privacy_mode=mode)
    )


def test_strictest_privacy_mode_wins_when_modes_are_mixed(
    project_root, fixtures, tmp_path
):
    mixed = (
        _with_mode(fixtures[0], PrivacyMode.FULL),
        _with_mode(fixtures[1], PrivacyMode.METADATA_ONLY),
        _with_mode(fixtures[2], PrivacyMode.REDACTED),
    )

    report = _run(
        project_root, mixed, stubs.perfect_pipeline(fixtures), tmp_path / "out"
    )

    assert report["privacy_mode"] == PrivacyMode.METADATA_ONLY.value
    # 느슨한 쪽을 택했다면 full 케이스의 diff 본문이 같은 파일에 실렸을 것이다.
    assert "generated_diff" not in _cases(report)[CASE1]
    assert "generated_files" not in _cases(report)[CASE1]


def test_strictest_privacy_mode_helper():
    assert rn.strictest_privacy_mode(()) is PrivacyMode.METADATA_ONLY


def test_strictest_privacy_mode_picks_metadata_only(fixtures):
    mixed = (
        _with_mode(fixtures[0], PrivacyMode.FULL),
        _with_mode(fixtures[1], PrivacyMode.REDACTED),
    )
    assert rn.strictest_privacy_mode(mixed) is PrivacyMode.REDACTED

    stricter = mixed + (_with_mode(fixtures[2], PrivacyMode.METADATA_ONLY),)
    assert rn.strictest_privacy_mode(stricter) is PrivacyMode.METADATA_ONLY


def test_explicit_privacy_mode_overrides_fixtures(project_root, fixtures, tmp_path):
    output_dir = tmp_path / "out"
    report = _run(
        project_root,
        fixtures,
        stubs.perfect_pipeline(fixtures),
        output_dir,
        privacy_mode=PrivacyMode.METADATA_ONLY,
    )

    assert report["privacy_mode"] == PrivacyMode.METADATA_ONLY.value
    environment = json.loads((output_dir / "environment.json").read_text(encoding="utf-8"))
    assert environment["privacy_mode_source"] == "override"


# ---------------------------------------------------------------------------
# git apply 실패는 예외가 아니라 지표다 (스펙 §7)
# ---------------------------------------------------------------------------


BROKEN_DIFF = (
    "--- a/src/main/java/com/example/tax/Missing.java\n"
    "+++ b/src/main/java/com/example/tax/Missing.java\n"
    "@@ -1,2 +1,2 @@\n"
    "-없는 파일의 첫 줄\n"
    "+바뀐 첫 줄\n"
    " 두 번째 줄\n"
)


def test_broken_diff_is_recorded_as_apply_failure(project_root, fixtures, tmp_path):
    def pipeline(context):
        return rn.PipelineOutput(diff_text=BROKEN_DIFF, retrieved_paths=())

    report = _run(project_root, fixtures[:1], pipeline, tmp_path / "out")

    case = _cases(report)[CASE1]
    assert case["git_apply"] is False
    # 실행 자체는 끝까지 갔다 — apply 실패는 실패 유형이 아니라 측정 결과다.
    assert case["failure_kind"] is None
    assert case["passed"] is False
    assert case["counts"]["generated_files"] == 0


def test_apply_failure_does_not_touch_the_original_repo(
    project_root, fixtures, tmp_path
):
    def pipeline(context):
        return rn.PipelineOutput(diff_text=BROKEN_DIFF, retrieved_paths=())

    before = _repo_state(_repo_path(project_root, "case1_value_change"))
    rn.run_fixtures(fixtures[:1], pipeline, project_root, tmp_path / "out")
    assert _repo_state(_repo_path(project_root, "case1_value_change")) == before


# ---------------------------------------------------------------------------
# seam 계약 (ADR-011)
# ---------------------------------------------------------------------------


def test_pipeline_receives_worktree_with_base_code(project_root, fixtures, tmp_path):
    seen: dict = {}

    def pipeline(context):
        seen["case_id"] = context.case_id
        seen["repo_id"] = context.repo_id
        seen["base_commit"] = context.base_commit
        seen["law_name"] = context.law.law_name
        seen["timeout"] = context.timeout_seconds
        seen["code"] = (context.worktree / CASE1_CODE).read_text(encoding="utf-8")
        seen["worktree"] = Path(context.worktree)
        return rn.PipelineOutput(diff_text="", retrieved_paths=())

    rn.run_fixtures(fixtures[:1], pipeline, project_root, tmp_path / "out")

    assert seen["case_id"] == CASE1
    assert seen["base_commit"] == "case1_value_change/base"
    assert seen["law_name"] == "소득세법"
    assert seen["timeout"] == 600
    # base 시점 코드다 — answer 값이 보이면 worktree 가 잘못 만들어진 것이다.
    assert "150000L" in seen["code"]
    assert "250000L" not in seen["code"]
    # worktree 는 실행이 끝나면 사라진다.
    assert not seen["worktree"].exists()


def test_repo_id_does_not_leak_the_repository_path(project_root, fixtures, tmp_path):
    seen: dict = {}

    def pipeline(context):
        seen["repo_id"] = context.repo_id
        return rn.PipelineOutput(diff_text="", retrieved_paths=())

    rn.run_fixtures(fixtures[:1], pipeline, project_root, tmp_path / "out")

    repo_id = seen["repo_id"]
    assert repo_id.startswith(f"{CASE1}:")
    assert str(project_root) not in repo_id
    assert "/" not in repo_id.split(":", 1)[1]


def test_repo_id_is_stable_for_the_same_repository(project_root):
    repo = _repo_path(project_root, "case1_value_change")
    assert rn.repo_identifier(CASE1, repo) == rn.repo_identifier(CASE1, repo)
    assert rn.repo_identifier(CASE1, repo) != rn.repo_identifier(CASE2, repo)


def test_runner_does_not_import_heavy_pipeline_dependencies():
    """seam 을 둔 이유가 사라지지 않았는지 소스로 확인한다 (ADR-011).

    import 하는 순간 replay 실행이 임베딩·벡터DB·추론 백엔드를 끌고 온다 — 집 환경
    테스트가 무거워지고 파이프라인 교체 지점이 흐려진다.
    """
    source = (PROJECT_ROOT / "app" / "evaluation" / "replay" / "runner.py").read_text(
        encoding="utf-8"
    )
    for banned in ("chromadb", "sentence_transformers", "anthropic", "app.llm"):
        assert banned not in source


def test_runner_does_not_call_subprocess_directly():
    """git 은 git_cmd, 골든은 golden_exec 만 실행한다 (ARCHITECTURE 레이어 규칙)."""
    source = (PROJECT_ROOT / "app" / "evaluation" / "replay" / "runner.py").read_text(
        encoding="utf-8"
    )
    assert "subprocess" not in source


# ---------------------------------------------------------------------------
# stub 파이프라인
# ---------------------------------------------------------------------------


@pytest.fixture
def base_snapshot(project_root, tmp_path) -> Path:
    """case1 의 base 시점 파일 하나를 스크래치 디렉토리에 펼친다 (stub 입력용)."""
    repo = _repo_path(project_root, "case1_value_change")
    text = _git(repo, "show", f"case1_value_change/base:{CASE1_CODE}")
    target = tmp_path / "snapshot" / CASE1_CODE
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")
    return tmp_path / "snapshot"


def test_stub_builds_diff_from_real_worktree_content(fixtures, tmp_path, base_snapshot):
    """stub 이 파일 내용을 실제로 읽는지 — 없는 파일이면 diff 가 비어야 한다."""
    replacements = fixtures[0].scope.expected_replacements
    empty_dir = tmp_path / "nothing"
    empty_dir.mkdir()

    assert stubs.build_diff(empty_dir, replacements) == ""

    diff_text = stubs.build_diff(base_snapshot, replacements)
    assert diff_text.startswith(f"--- a/{CASE1_CODE}")
    assert "-    public static final long CHILD_TAX_CREDIT = 150000L;" in diff_text
    assert "+    public static final long CHILD_TAX_CREDIT = 250000L;" in diff_text


def test_stub_limit_zero_produces_no_diff(fixtures, base_snapshot):
    replacements = fixtures[0].scope.expected_replacements
    assert stubs.build_diff(base_snapshot, replacements, limit=0) == ""


def test_unknown_case_gets_an_empty_draft(project_root, fixtures, tmp_path):
    """stub 은 모르는 케이스에 답을 지어내지 않는다."""
    pipeline = stubs.perfect_pipeline(())
    report = _run(project_root, fixtures[:1], pipeline, tmp_path / "out")
    assert _cases(report)[CASE1]["metrics"]["file_coverage"] == 0.0


def test_build_stub_pipeline_names(fixtures):
    assert sorted(stubs.STUB_PIPELINES) == ["empty", "partial", "perfect"]
    for name in stubs.STUB_PIPELINES:
        assert callable(stubs.build_stub_pipeline(name, fixtures))
    with pytest.raises(KeyError):
        stubs.build_stub_pipeline("nope", fixtures)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_requires_an_explicit_pipeline(capsys, tmp_path):
    """실제 파이프라인을 기본값으로 붙이지 않는다 (ADR-011)."""
    exit_code = rn.main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 2
    assert not (tmp_path / "out").exists()
    assert "ERROR" in capsys.readouterr().err


def test_cli_rejects_unknown_stub(capsys, tmp_path):
    exit_code = rn.main(["--stub", "magic", "--output-dir", str(tmp_path / "out")])
    assert exit_code == 2
    assert "magic" in capsys.readouterr().err


def test_cli_runs_the_stub_and_writes_a_report(monkeypatch, project_root, tmp_path):
    monkeypatch.setattr(rn, "PROJECT_ROOT", project_root)
    output_dir = tmp_path / "cli-out"

    exit_code = rn.main(
        [
            "--fixtures",
            str(MOCK_FIXTURE_PATH),
            "--output-dir",
            str(output_dir),
            "--stub",
            "perfect",
        ]
    )

    assert exit_code == 0
    report = _read_report(output_dir)
    assert report["summary"]["case_count"] == 3
    assert report["summary"]["pass_rate"] == 1.0


def test_cli_privacy_mode_option(monkeypatch, project_root, tmp_path):
    monkeypatch.setattr(rn, "PROJECT_ROOT", project_root)
    output_dir = tmp_path / "cli-out"

    exit_code = rn.main(
        [
            "--fixtures",
            str(MOCK_FIXTURE_PATH),
            "--output-dir",
            str(output_dir),
            "--stub",
            "empty",
            "--privacy-mode",
            "metadata_only",
        ]
    )

    assert exit_code == 0
    assert _read_report(output_dir)["privacy_mode"] == "metadata_only"


def test_cli_reports_invalid_fixture_file(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("cases:\n  - case_id: broken\n", encoding="utf-8")

    exit_code = rn.main(
        ["--fixtures", str(bad), "--output-dir", str(tmp_path / "out"), "--stub", "empty"]
    )

    assert exit_code == 1
    assert "ERROR" in capsys.readouterr().err
