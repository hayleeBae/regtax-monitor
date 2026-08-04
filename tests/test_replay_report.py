"""replay 지표 산출·privacy 게이팅 테스트 — HISTORICAL_REPLAY_SPEC §7·§8, Issue #0018 Step 3.

git·임베딩·LLM 을 쓰지 않는다. 지표 계산은 순수 함수이고 저장은 `tmp_path` 만 쓴다.
"""

from __future__ import annotations

import json

import pytest

from app.evaluation.case import ExpectedReplacement
from app.evaluation.replay.answer_diff import AnswerDiff, ChangedFile, ReplacementCheck
from app.evaluation.replay.fixture import ArtifactKind, PrivacyMode, allowed_artifacts
from app.evaluation.replay.report import (
    GeneratedReplacement,
    ReplayOutcome,
    build_report,
    changed_file_jaccard,
    compute_metrics,
    normalized_diff_similarity,
    path_hash,
    primary_rank,
    summarize,
    write_report,
)

# 리포트 본문에서 찾기 쉬운 토큰들 — 다른 필드에 우연히 섞이지 않는 문자열을 쓴다.
RELEVANT_PATH = "module-tax/src/main/java/TaxService.java"
EXCLUDED_PATH = "README.md"
UNRELATED_PATH = "module-hr/src/main/java/LeaveService.java"
BEFORE_TOKEN = "LIMIT_BEFORE_TOKEN"
AFTER_TOKEN = "LIMIT_AFTER_TOKEN"
DIFF_BODY_TOKEN = "DIFF_BODY_SECRET_LINE"
GOLDEN_TOKEN = "GOLDEN_STACKTRACE_TOKEN"


def changed(path: str, status: str = "M", added: int = 3, removed: int = 1) -> ChangedFile:
    return ChangedFile(path=path, status=status, added_lines=added, removed_lines=removed)


def make_outcome(**overrides) -> ReplayOutcome:
    """모든 지표가 만점이 되는 기준 케이스 — 테스트마다 필요한 축만 어긋뜨린다."""
    defaults = dict(
        case_id="case_base",
        answer=AnswerDiff(
            in_scope=(changed(RELEVANT_PATH),),
            out_of_scope=(changed(UNRELATED_PATH),),
            excluded=(changed(EXCLUDED_PATH),),
        ),
        replacement_checks=(
            ReplacementCheck(
                path=RELEVANT_PATH,
                match_mode="exact",
                path_exists=True,
                found_after=True,
                found_before=False,
            ),
        ),
        expected_replacements=(
            ExpectedReplacement(
                path=RELEVANT_PATH, before=BEFORE_TOKEN, after=AFTER_TOKEN
            ),
        ),
        generated_files=(RELEVANT_PATH,),
        generated_replacements=(
            GeneratedReplacement(
                path=RELEVANT_PATH, before=BEFORE_TOKEN, after=AFTER_TOKEN
            ),
        ),
        retrieved_paths=(RELEVANT_PATH, UNRELATED_PATH),
        git_apply_ok=True,
        golden_status="passed",
        golden_output=f"OK\n{GOLDEN_TOKEN}\n",
        generated_diff=f"--- a/x\n+++ b/x\n-{DIFF_BODY_TOKEN}\n+{AFTER_TOKEN}\n",
        answer_diff_text=f"--- a/x\n+++ b/x\n-{DIFF_BODY_TOKEN}\n+{AFTER_TOKEN}\n",
        duration_ms=1200,
    )
    defaults.update(overrides)
    return ReplayOutcome(**defaults)


# ---------------------------------------------------------------------------
# 지표 계산 (순수 함수)
# ---------------------------------------------------------------------------


def test_recall_at_k_uses_rank_position():
    # 정답 2개, 검색 결과는 [무관, 정답A, 무관, 정답B]
    outcome = make_outcome(
        answer=AnswerDiff(
            in_scope=(changed("a.java"), changed("b.java")),
            out_of_scope=(),
            excluded=(),
        ),
        retrieved_paths=("x.java", "a.java", "y.java", "b.java"),
        expected_replacements=(),
        generated_replacements=(),
        replacement_checks=(),
        generated_files=("a.java", "b.java"),
    )
    recall = compute_metrics(outcome).relevant_path_recall_at_k
    assert recall[1] == 0.0
    assert recall[3] == pytest.approx(0.5)
    assert recall[5] == 1.0
    assert recall[10] == 1.0


def test_primary_rank_is_first_hit_position():
    outcome = make_outcome(
        answer=AnswerDiff(in_scope=(changed("b.java"),), out_of_scope=(), excluded=()),
        retrieved_paths=("x.java", "y.java", "b.java"),
    )
    assert compute_metrics(outcome).primary_rank == 3


def test_primary_rank_is_none_when_no_hit():
    outcome = make_outcome(retrieved_paths=("x.java", "y.java"))
    assert compute_metrics(outcome).primary_rank is None


def test_primary_rank_helper_matches_metrics():
    assert primary_rank(("b",), ("a", "b", "c")) == 2
    assert primary_rank(("z",), ("a", "b")) is None
    assert primary_rank((), ("a",)) is None


def test_file_coverage_is_ratio_of_in_scope_touched():
    outcome = make_outcome(
        answer=AnswerDiff(
            in_scope=(changed("a.java"), changed("b.java"), changed("c.java")),
            out_of_scope=(),
            excluded=(),
        ),
        generated_files=("a.java", "b.java"),
    )
    assert compute_metrics(outcome).file_coverage == pytest.approx(2 / 3)


def test_unnecessary_file_rate_ignores_excluded_paths():
    # 초안이 4개를 건드렸고 그중 in-scope 1 / excluded 1 / 무관 2 → 0.5
    outcome = make_outcome(
        answer=AnswerDiff(
            in_scope=(changed("a.java"),),
            out_of_scope=(),
            excluded=(changed(EXCLUDED_PATH),),
        ),
        generated_files=("a.java", EXCLUDED_PATH, "z1.java", "z2.java"),
    )
    assert compute_metrics(outcome).unnecessary_file_rate == pytest.approx(0.5)


def test_changed_file_jaccard_intersection_over_union():
    # 초안 {a,b,z} ∩ 정답 {a,b,c} = 2, 합집합 4 → 0.5
    assert changed_file_jaccard(("a", "b", "z"), ("a", "b", "c")) == pytest.approx(0.5)
    assert changed_file_jaccard((), ()) == 1.0
    assert changed_file_jaccard(("a",), ()) == 0.0


def test_changed_file_jaccard_normalizes_paths():
    assert changed_file_jaccard(("./src/a.java",), ("src\\a.java",)) == 1.0


def test_expected_replacement_accuracy_counts_performed_ones():
    outcome = make_outcome(
        expected_replacements=(
            ExpectedReplacement(path="a.java", before="1", after="2"),
            ExpectedReplacement(path="b.java", before="3", after="4"),
        ),
        generated_replacements=(
            GeneratedReplacement(path="a.java", before="1", after="2"),
        ),
    )
    metrics = compute_metrics(outcome)
    assert metrics.expected_replacement_accuracy == pytest.approx(0.5)
    assert [item.matched for item in metrics.replacements] == [True, False]


def test_expected_replacement_requires_same_path():
    """다른 파일에서 같은 값을 바꿨다면 그 교체를 수행한 것이 아니다."""
    outcome = make_outcome(
        expected_replacements=(
            ExpectedReplacement(path="a.java", before="150000", after="250000"),
        ),
        generated_replacements=(
            GeneratedReplacement(path="other.java", before="150000", after="250000"),
        ),
    )
    assert compute_metrics(outcome).expected_replacement_accuracy == 0.0


def test_expected_replacement_normalized_text_mode():
    outcome = make_outcome(
        expected_replacements=(
            ExpectedReplacement(
                path="a.java",
                before="int  limit =   150000;",
                after="int limit = 250000;",
                match_mode="normalized_text",
            ),
        ),
        generated_replacements=(
            GeneratedReplacement(
                path="a.java", before="int limit = 150000;", after="int  limit = 250000;"
            ),
        ),
    )
    assert compute_metrics(outcome).expected_replacement_accuracy == 1.0


def test_no_expected_replacements_is_full_accuracy():
    outcome = make_outcome(expected_replacements=(), generated_replacements=())
    assert compute_metrics(outcome).expected_replacement_accuracy == 1.0


def test_git_apply_and_golden_are_recorded_as_is():
    metrics = compute_metrics(make_outcome(git_apply_ok=None, golden_status=None))
    assert metrics.git_apply is None
    assert metrics.golden_result is None


def test_metrics_do_not_touch_filesystem(monkeypatch):
    """지표 계산은 순수 함수다 — git wrapper 를 부르면 실패해야 한다."""

    def explode(*args, **kwargs):  # pragma: no cover - 호출되면 실패
        raise AssertionError("지표 계산이 git 을 호출했다")

    monkeypatch.setattr("app.evaluation.replay.git_cmd.run_git", explode)
    assert compute_metrics(make_outcome()).passed is True


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def test_verdict_fails_on_missing_coverage():
    outcome = make_outcome(generated_files=())
    assert compute_metrics(outcome).passed is False


def test_verdict_fails_on_git_apply_false():
    assert compute_metrics(make_outcome(git_apply_ok=False)).passed is False


def test_verdict_fails_on_golden_failure():
    assert compute_metrics(make_outcome(golden_status="failed")).passed is False
    assert compute_metrics(make_outcome(golden_status="error")).passed is False
    assert compute_metrics(make_outcome(golden_status="skipped")).passed is True


def test_verdict_fails_when_fixture_is_inconsistent():
    broken = ReplacementCheck(
        path=RELEVANT_PATH,
        match_mode="exact",
        path_exists=False,
        found_after=False,
        found_before=False,
    )
    assert compute_metrics(make_outcome(replacement_checks=(broken,))).passed is False


def test_unrun_stages_do_not_fail_the_case():
    outcome = make_outcome(git_apply_ok=None, golden_status=None)
    assert compute_metrics(outcome).passed is True


# ---------------------------------------------------------------------------
# normalized diff similarity — 참고값
# ---------------------------------------------------------------------------


def test_normalized_diff_similarity_ignores_whitespace():
    assert normalized_diff_similarity("a = 1\nb = 2\n", "a  =  1\n  b = 2\n") == 1.0


def test_normalized_diff_similarity_zero_without_input():
    assert normalized_diff_similarity(None, "a") == 0.0
    assert normalized_diff_similarity("a", None) == 0.0
    assert normalized_diff_similarity("", "") == 0.0


def test_similarity_does_not_change_verdict():
    """유사도만 0 으로 만든 입력에서도 판정과 나머지 지표가 그대로여야 한다 (스펙 §7)."""
    similar = compute_metrics(make_outcome())
    dissimilar = compute_metrics(
        make_outcome(generated_diff="완전히 다른 내용\n", answer_diff_text="another\n")
    )
    assert similar.normalized_diff_similarity != dissimilar.normalized_diff_similarity
    assert similar.passed == dissimilar.passed is True
    assert similar.file_coverage == dissimilar.file_coverage
    assert similar.changed_file_jaccard == dissimilar.changed_file_jaccard
    assert (
        similar.expected_replacement_accuracy
        == dissimilar.expected_replacement_accuracy
    )


def test_similarity_absence_does_not_fail_the_case():
    outcome = make_outcome(generated_diff=None, answer_diff_text=None)
    metrics = compute_metrics(outcome)
    assert metrics.normalized_diff_similarity == 0.0
    assert metrics.passed is True


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------


def test_summarize_empty_is_zero_filled():
    summary = summarize([])
    assert summary["case_count"] == 0
    assert summary["pass_rate"] == 0.0
    assert summary["recall_at_k"]["5"] == 0.0


def test_summarize_averages_cases():
    passed = compute_metrics(make_outcome(case_id="ok"))
    failed = compute_metrics(make_outcome(case_id="ng", generated_files=()))
    summary = summarize([passed, failed])
    assert summary["case_count"] == 2
    assert summary["passed_count"] == 1
    assert summary["pass_rate"] == pytest.approx(0.5)
    assert summary["file_coverage"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# privacy 게이팅 — 저장된 파일 내용을 직접 검사한다
# ---------------------------------------------------------------------------


def written_text(tmp_path, mode: PrivacyMode, outcomes=None) -> str:
    """리포트 3개 파일의 내용을 이어붙인 문자열 — 자료구조가 아니라 실제 저장물을 본다."""
    target = write_report(
        outcomes if outcomes is not None else [make_outcome(case_id="case_privacy")],
        tmp_path / mode.value,
        mode,
    )
    return "\n".join(
        (target / name).read_text(encoding="utf-8")
        for name in ("replay_report.json", "replay_report.md", "environment.json")
    )


def test_metadata_only_writes_no_paths_no_code(tmp_path):
    text = written_text(tmp_path, PrivacyMode.METADATA_ONLY)
    for secret in (
        RELEVANT_PATH,
        "TaxService.java",
        EXCLUDED_PATH,
        UNRELATED_PATH,
        BEFORE_TOKEN,
        AFTER_TOKEN,
        DIFF_BODY_TOKEN,
        GOLDEN_TOKEN,
    ):
        assert secret not in text, f"metadata_only 에 {secret} 이(가) 남았다"


def test_metadata_only_keeps_metrics_counts_and_hashes(tmp_path):
    target = write_report(
        [make_outcome(case_id="case_privacy")],
        tmp_path / "meta",
        PrivacyMode.METADATA_ONLY,
    )
    report = json.loads((target / "replay_report.json").read_text(encoding="utf-8"))
    case = report["cases"][0]
    assert case["case_id"] == "case_privacy"
    assert case["counts"]["answer_in_scope_files"] == 1
    assert case["metrics"]["file_coverage"] == 1.0
    assert case["golden_result"] == "passed"
    assert case["git_apply"] is True
    assert "answer_files" not in case
    assert "generated_files" not in case
    assert "generated_diff" not in case
    assert "golden_output" not in case
    assert case["retrieved"][0]["path_hash"] == path_hash(RELEVANT_PATH)
    assert "path" not in case["retrieved"][0]
    assert case["expected_replacements"][0]["matched"] is True
    assert "before" not in case["expected_replacements"][0]


def test_redacted_keeps_paths_and_line_counts_but_no_code(tmp_path):
    target = write_report(
        [make_outcome(case_id="case_privacy")],
        tmp_path / "redacted",
        PrivacyMode.REDACTED,
    )
    text = (target / "replay_report.json").read_text(encoding="utf-8")
    case = json.loads(text)["cases"][0]

    in_scope = case["answer_files"]["in_scope"][0]
    assert in_scope["path"] == RELEVANT_PATH
    assert (in_scope["added_lines"], in_scope["removed_lines"]) == (3, 1)
    assert case["generated_files"][0]["path"] == RELEVANT_PATH

    for secret in (BEFORE_TOKEN, AFTER_TOKEN, DIFF_BODY_TOKEN, GOLDEN_TOKEN):
        assert secret not in text
    assert "generated_diff" not in case
    assert "golden_output" not in case
    assert "before" not in case["expected_replacements"][0]
    assert case["expected_replacements"][0]["matched"] is True
    # 상태 문자열은 본문이 아니므로 남는다.
    assert case["golden_result"] == "passed"


def test_full_keeps_everything(tmp_path):
    target = write_report(
        [make_outcome(case_id="case_privacy")], tmp_path / "full", PrivacyMode.FULL
    )
    text = (target / "replay_report.json").read_text(encoding="utf-8")
    case = json.loads(text)["cases"][0]
    assert case["answer_files"]["in_scope"][0]["path"] == RELEVANT_PATH
    assert case["answer_files"]["excluded"][0]["path"] == EXCLUDED_PATH
    assert DIFF_BODY_TOKEN in case["generated_diff"]
    assert DIFF_BODY_TOKEN in case["answer_diff"]
    assert GOLDEN_TOKEN in case["golden_output"]
    assert case["expected_replacements"][0]["before"] == BEFORE_TOKEN
    assert case["expected_replacements"][0]["after"] == AFTER_TOKEN


def test_report_records_the_allowed_artifact_set(tmp_path):
    for mode in PrivacyMode:
        report = build_report([make_outcome()], mode)
        assert report["privacy_mode"] == mode.value
        assert report["allowed_artifacts"] == sorted(
            kind.value for kind in allowed_artifacts(mode)
        )


def test_gating_follows_allowed_artifacts_not_a_local_copy(monkeypatch):
    """게이팅 판단은 #0017 의 `allowed_artifacts()` 하나만 본다."""
    monkeypatch.setattr(
        "app.evaluation.replay.report.allowed_artifacts",
        lambda mode: frozenset({ArtifactKind.METRIC, ArtifactKind.HASH, ArtifactKind.COUNT}),
    )
    case = build_report([make_outcome()], PrivacyMode.FULL)["cases"][0]
    assert "answer_files" not in case
    assert "generated_diff" not in case
    assert "golden_output" not in case
    assert "path" not in case["retrieved"][0]


def test_path_hash_is_stable_and_normalized():
    assert path_hash(RELEVANT_PATH) == path_hash(f"./{RELEVANT_PATH}")
    assert path_hash("a.java") != path_hash("b.java")
    assert RELEVANT_PATH not in path_hash(RELEVANT_PATH)


# ---------------------------------------------------------------------------
# 저장 형식
# ---------------------------------------------------------------------------


def test_write_report_creates_three_files(tmp_path):
    target = write_report(
        [make_outcome()], tmp_path / "run", PrivacyMode.REDACTED, {"note": "step3"}
    )
    assert sorted(item.name for item in target.iterdir()) == [
        "environment.json",
        "replay_report.json",
        "replay_report.md",
    ]
    environment = json.loads((target / "environment.json").read_text(encoding="utf-8"))
    assert environment["privacy_mode"] == "redacted"
    assert environment["note"] == "step3"
    assert environment["recall_k"] == [1, 3, 5, 10]


def test_markdown_marks_similarity_as_reference_only(tmp_path):
    target = write_report([make_outcome()], tmp_path / "run", PrivacyMode.REDACTED)
    text = (target / "replay_report.md").read_text(encoding="utf-8")
    assert "참고값" in text
    assert "case_base" in text


def test_same_input_writes_identical_bytes(tmp_path):
    first = write_report([make_outcome()], tmp_path / "a", PrivacyMode.FULL)
    second = write_report([make_outcome()], tmp_path / "b", PrivacyMode.FULL)
    for name in ("replay_report.json", "replay_report.md", "environment.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_empty_outcomes_write_an_empty_report(tmp_path):
    target = write_report([], tmp_path / "empty", PrivacyMode.METADATA_ONLY)
    report = json.loads((target / "replay_report.json").read_text(encoding="utf-8"))
    assert report["cases"] == []
    assert report["summary"]["case_count"] == 0
    assert (target / "replay_report.md").read_text(encoding="utf-8").startswith(
        "# Historical Replay Report"
    )


def test_write_report_accepts_mode_as_string(tmp_path):
    target = write_report([make_outcome()], tmp_path / "str", "metadata_only")
    report = json.loads((target / "replay_report.json").read_text(encoding="utf-8"))
    assert report["privacy_mode"] == "metadata_only"
