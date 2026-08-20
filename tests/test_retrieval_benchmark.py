"""Issue #0010 검색 조합 비교 benchmark 테스트 (+ #0016 rerank ablation)."""

from pathlib import Path

import pytest

from app.domain.common.enums import RetrievalSource
from app.domain.mappings.decisions import MappingDecisionType
from app.domain.mappings.reranking import RERANK_VERSION, DecisionContext
from app.domain.retrieval import (
    CandidateLocation,
    RetrievalCandidate,
    RetrievalEvidence,
)
from app.evaluation.case import (
    CaseMetadata,
    EvaluationCase,
    ExecutionExpectation,
    ExpectedOutcome,
    ExpectedRetrieval,
    LawInput,
    RepositoryFixture,
)
from app.evaluation.decision_fixtures import (
    FixtureDecisionReranker,
    load_decision_fixtures,
)
from app.evaluation.retrieval_benchmark import (
    BenchmarkCase,
    RetrievalBenchmark,
    case_article_id,
    default_experiments,
    ensure_benchmark_index,
    run_orchestrator_cases,
)


def _run_variant(experiment):
    predictions = {
        "rag_only": ("wrong.java", "A.java"),
        "rag_dict": ("A.java",),
        "rag_const": ("A.java", "B.java"),
        "hybrid_all": ("A.java", "B.java"),
        "verified_hybrid": ("A.java", "B.java"),
        "verified_rerank_off": ("A.java", "B.java"),
        "verified_rerank_on": ("A.java", "B.java"),
        # graph_off는 verified_rerank_on과 조건이 완전히 같아 결과도 같아야 한다(회귀 고정).
        "graph_off": ("A.java", "B.java"),
        # graph_hybrid는 recall 이득 없이 불필요 후보(NoiseTest.java)만 늘어나는 사례를
        # 시뮬레이션한다 — ablation은 이런 결과도 숨기지 않고 그대로 보고해야 한다.
        "graph_hybrid": ("A.java", "B.java", "NoiseTest.java"),
    }
    return (
        BenchmarkCase("case-1", ("A.java",), predictions[experiment.experiment_id], 10),
        BenchmarkCase("case-2", ("B.java",), predictions[experiment.experiment_id], 20),
    )


def test_default_experiments_have_fixed_provider_combinations() -> None:
    experiments = default_experiments()
    assert [item.experiment_id for item in experiments] == [
        "rag_only", "rag_dict", "rag_const", "hybrid_all", "verified_hybrid",
        "verified_rerank_off", "verified_rerank_on",
        "graph_off", "graph_hybrid",
    ]
    assert experiments[0].enabled_sources == ("rag",)
    assert "verified_mapping" in experiments[4].enabled_sources


def test_graph_ablation_pair_differs_only_by_graph_flag() -> None:
    """공정 비교 고정 — 조건이 graph_enabled 외에 다르면 차이의 원인을 특정할 수 없다."""
    by_id = {item.experiment_id: item for item in default_experiments()}
    off = by_id["graph_off"]
    on = by_id["graph_hybrid"]

    assert off.enabled_sources == on.enabled_sources
    assert off.top_k == on.top_k
    assert off.scoring_version == on.scoring_version
    assert off.normalization_version == on.normalization_version
    assert off.rerank_enabled == on.rerank_enabled
    assert (off.graph_enabled, on.graph_enabled) == (False, True)


def test_graph_off_matches_pre_graph_baseline() -> None:
    """graph_off는 graph 도입 전 기준선(verified_rerank_on)과 조건이 동일하다."""
    by_id = {item.experiment_id: item for item in default_experiments()}
    baseline = by_id["verified_rerank_on"]
    graph_off = by_id["graph_off"]

    assert graph_off.enabled_sources == baseline.enabled_sources
    assert graph_off.top_k == baseline.top_k
    assert graph_off.rerank_enabled == baseline.rerank_enabled
    assert graph_off.graph_enabled is False == baseline.graph_enabled


def test_graph_ablation_report_shows_off_on_metrics_and_mock_disclaimer(
    tmp_path: Path,
) -> None:
    result = RetrievalBenchmark(_run_variant).run(tmp_path, "graph-ablation")

    # off 조건은 그래프 도입 전 기준선(verified_rerank_on)과 지표가 완전히 일치해야 한다.
    assert result.summary["graph_off"] == result.summary["verified_rerank_on"]

    # graph_hybrid는 recall 이득이 없고 불필요 후보·candidate 수만 늘었다 — 숨기지 않고 보고한다.
    off_metrics = result.summary["graph_off"]
    on_metrics = result.summary["graph_hybrid"]
    assert on_metrics["recall_at_5"] == off_metrics["recall_at_5"]
    assert on_metrics["unnecessary_file_rate"] > off_metrics["unnecessary_file_rate"]
    assert on_metrics["average_candidate_count"] > off_metrics["average_candidate_count"]

    report = (result.output_dir / "comparison.md").read_text(encoding="utf-8")
    assert "graph_off" in report and "graph_hybrid" in report
    assert "Unnecessary File Rate" in report and "Candidate Count" in report
    assert "합성 mock 기준" in report


def test_test_file_recall_counts_only_test_files() -> None:
    from app.evaluation.retrieval_benchmark import _aggregate

    cases = (
        BenchmarkCase("c1", ("A.java", "ATest.java"), ("A.java", "ATest.java"), 10),
        BenchmarkCase("c2", ("B.java",), ("wrong.java",), 10),
    )
    summary = _aggregate(cases)
    # c1: test 파일(ATest.java) 1개 중 1개 회수 = 1.0. c2: test 관련 정답 없음 → 1.0(vacuous).
    assert summary["test_file_recall_at_5"] == 1.0
    assert summary["average_candidate_count"] == 1.5


def test_rerank_ablation_pair_differs_only_by_rerank_flag() -> None:
    """공정 비교 고정 — 조건이 rerank 외에 다르면 차이의 원인을 특정할 수 없다."""
    by_id = {item.experiment_id: item for item in default_experiments()}
    off = by_id["verified_rerank_off"]
    on = by_id["verified_rerank_on"]

    assert off.enabled_sources == on.enabled_sources
    assert off.top_k == on.top_k
    assert off.scoring_version == on.scoring_version
    assert off.normalization_version == on.normalization_version
    assert (off.rerank_enabled, on.rerank_enabled) == (False, True)


def test_legacy_experiments_keep_rerank_disabled() -> None:
    """기존 5개 실험은 과거 결과와 비교 가능해야 하므로 rerank 없이 측정한다."""
    legacy = default_experiments()[:5]
    assert all(item.rerank_enabled is False for item in legacy)


def test_benchmark_writes_comparison_and_case_rank_differences(tmp_path: Path) -> None:
    result = RetrievalBenchmark(_run_variant).run(tmp_path, "test-benchmark")

    assert (result.output_dir / "comparison.json").is_file()
    report = (result.output_dir / "comparison.md").read_text(encoding="utf-8")
    assert "Recall@1" in report and "MRR" in report
    assert "rag_only" in report and "hybrid_all" in report
    assert result.summary["rag_only"]["recall_at_1"] < result.summary["rag_dict"]["recall_at_1"]
    rank_rows = (result.output_dir / "case_ranks.jsonl").read_text(encoding="utf-8")
    assert '"case_id":"case-1"' in rank_rows
    assert (result.output_dir / "environment.json").is_file()


def test_same_cases_produce_deterministic_comparison_files(tmp_path: Path) -> None:
    first = RetrievalBenchmark(_run_variant).run(tmp_path / "a", "stable")
    second = RetrievalBenchmark(_run_variant).run(tmp_path / "b", "stable")
    for name in ("comparison.json", "comparison.md", "case_ranks.jsonl"):
        assert (first.output_dir / name).read_bytes() == (second.output_dir / name).read_bytes()


def test_empty_benchmark_index_requires_explicit_build() -> None:
    class Collection:
        value = 0
        def count(self):
            return self.value
    class Indexer:
        collection = Collection()
        def index(self, adapter):
            self.collection.value = 3
            return 3
    indexer = Indexer()
    try:
        ensure_benchmark_index(indexer, object(), False)
    except RuntimeError as exc:
        assert "--build-index" in str(exc)
    else:
        raise AssertionError("empty index must require --build-index")
    assert ensure_benchmark_index(indexer, object(), True) == 3


def test_environment_records_rerank_flag_and_version(tmp_path: Path) -> None:
    import json

    result = RetrievalBenchmark(_run_variant).run(tmp_path, "env-run")
    environment = json.loads(
        (result.output_dir / "environment.json").read_text(encoding="utf-8")
    )
    assert environment["rerank_version"] == RERANK_VERSION
    flags = {
        item["experiment_id"]: item["rerank_enabled"]
        for item in environment["experiments"]
    }
    assert flags["verified_rerank_off"] is False
    assert flags["verified_rerank_on"] is True
    graph_flags = {
        item["experiment_id"]: item["graph_enabled"]
        for item in environment["experiments"]
    }
    assert graph_flags["graph_off"] is False
    assert graph_flags["graph_hybrid"] is True


# ---------------------------------------------------------------------------
# #0016 결정 이력 fixture (DB 없이 rerank ablation)
# ---------------------------------------------------------------------------


FIXTURE_YAML = """
decisions:
  - path: "tax/TaxConstants.java"
    article_id: "소득세법:제59조의2"
    change_type: "value_change"
    state: "verified"
    reason_code: "confirmed_by_owner"
  - path: "salary/HrConstants.java"
    symbol: "HrConstants.MINIMUM_HOURLY_WAGE"
    article_id: "소득세법:제59조의2"
    change_type: "value_change"
    state: "rejected"
    reason_code: "wrong_module"
    rejection_count: 2
"""


def _candidate(path: str, symbol: str | None = None) -> RetrievalCandidate:
    evidence = RetrievalEvidence(RetrievalSource.RAG, 0.5, 0.5)
    return RetrievalCandidate(CandidateLocation(path, symbol), (evidence,), 0.5)


def test_fixture_loader_builds_domain_decision_contexts(tmp_path: Path) -> None:
    path = tmp_path / "decisions.yaml"
    path.write_text(FIXTURE_YAML, encoding="utf-8")

    entries = load_decision_fixtures(path)

    assert [item.path for item in entries] == [
        "tax/TaxConstants.java", "salary/HrConstants.java"
    ]
    assert entries[0].symbol is None
    assert entries[1].symbol == "HrConstants.MINIMUM_HOURLY_WAGE"
    verified, rejected = entries[0].context, entries[1].context
    assert isinstance(verified, DecisionContext)
    assert verified.state is MappingDecisionType.VERIFIED
    assert verified.article_id == "소득세법:제59조의2"
    assert verified.change_type == "value_change"
    assert verified.rejection_count == 0
    assert rejected.state is MappingDecisionType.REJECTED
    assert rejected.rejection_count == 2


def test_fixture_loader_rejects_missing_file_and_accepts_empty(tmp_path: Path) -> None:
    # 오타를 "이력 없음"으로 삼키면 rerank 차이 0을 잘못 해석하게 된다.
    with pytest.raises(FileNotFoundError):
        load_decision_fixtures(tmp_path / "absent.yaml")

    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert load_decision_fixtures(empty) == ()

    blank = tmp_path / "blank.yaml"
    blank.write_text("decisions:\n", encoding="utf-8")
    assert load_decision_fixtures(blank) == ()


def test_shipped_core_decisions_fixture_loads() -> None:
    project_root = Path(__file__).resolve().parents[1]
    entries = load_decision_fixtures(
        project_root / "evaluation/fixtures/decisions/core_decisions.yaml"
    )
    states = [item.context.state for item in entries]
    assert MappingDecisionType.VERIFIED in states
    assert MappingDecisionType.REJECTED in states
    assert MappingDecisionType.STALE in states


def test_fixture_reranker_keys_contexts_by_candidate_dedup_key(tmp_path: Path) -> None:
    path = tmp_path / "decisions.yaml"
    path.write_text(FIXTURE_YAML, encoding="utf-8")
    reranker = FixtureDecisionReranker(load_decision_fixtures(path))

    matched = _candidate("tax/TaxConstants.java", "TaxConstants.CHILD_TAX_CREDIT")
    other_symbol = _candidate("salary/HrConstants.java", "HrConstants.ANNUAL_LEAVE")
    unrelated = _candidate("tax/Other.java")

    contexts = reranker.contexts_for(object(), (matched, other_symbol, unrelated))

    assert reranker.version == RERANK_VERSION
    # symbol 없는 항목은 파일 전체에 적용된다.
    assert set(contexts) == {matched.dedup_key}
    assert contexts[matched.dedup_key][0].state is MappingDecisionType.VERIFIED
    # symbol 이 명시된 항목은 다른 symbol 후보에 붙지 않는다.
    assert other_symbol.dedup_key not in contexts
    assert unrelated.dedup_key not in contexts


def test_fixture_reranker_matches_named_symbol(tmp_path: Path) -> None:
    path = tmp_path / "decisions.yaml"
    path.write_text(FIXTURE_YAML, encoding="utf-8")
    reranker = FixtureDecisionReranker(load_decision_fixtures(path))

    candidate = _candidate(
        "salary/HrConstants.java", "HrConstants.MINIMUM_HOURLY_WAGE"
    )
    contexts = reranker.contexts_for(object(), (candidate,))
    assert contexts[candidate.dedup_key][0].state is MappingDecisionType.REJECTED


# ---------------------------------------------------------------------------
# #0016 runner 배선 (가짜 orchestrator — 임베딩·DB·LLM 없음)
# ---------------------------------------------------------------------------


class _RecordingOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def retrieve(self, query, config):
        self.calls.append((query, config))

        class _Response:
            candidates = ()
            provider_statuses: dict = {}

        return _Response()


def _case(case_id: str, article, change_type: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        title=case_id,
        domain="tax",
        tags=(),
        law=LawInput("소득세법", "law", "before", "after", article=article),
        expected=ExpectedOutcome(
            change_type=change_type,
            retrieval=ExpectedRetrieval(("tax/TaxConstants.java",)),
        ),
        repository=RepositoryFixture("directory", "evaluation/fixtures"),
        execution=ExecutionExpectation(),
        metadata=CaseMetadata("synthetic"),
    )


def test_case_article_id_needs_an_article() -> None:
    assert case_article_id(_case("c1", "제59조의2", "value_change")) == (
        "소득세법:제59조의2"
    )
    assert case_article_id(_case("c2", None, "rate_change")) is None
    assert case_article_id(_case("c3", "   ", "rate_change")) is None


def test_runner_passes_query_context_and_rerank_flag() -> None:
    orchestrator = _RecordingOrchestrator()
    cases = (
        _case("c1", "제59조의2", "value_change"),
        _case("c2", None, "rate_change"),
    )
    by_id = {item.experiment_id: item for item in default_experiments()}

    run_orchestrator_cases(orchestrator, cases, by_id["verified_rerank_on"])
    run_orchestrator_cases(orchestrator, cases, by_id["verified_rerank_off"])

    on_first, on_second = orchestrator.calls[0], orchestrator.calls[1]
    assert on_first[0].article_id == "소득세법:제59조의2"
    assert on_first[0].change_type == "value_change"
    assert on_second[0].article_id is None
    assert on_first[1].rerank_enabled is True
    assert orchestrator.calls[2][1].rerank_enabled is False
    # 공정 비교: rerank 외 조건은 두 변형에서 동일해야 한다.
    assert on_first[1].enabled_sources == orchestrator.calls[2][1].enabled_sources
    assert on_first[1].final_top_k == orchestrator.calls[2][1].final_top_k


def test_runner_passes_graph_enabled_flag() -> None:
    orchestrator = _RecordingOrchestrator()
    cases = (_case("c1", "제59조의2", "value_change"),)
    by_id = {item.experiment_id: item for item in default_experiments()}

    run_orchestrator_cases(orchestrator, cases, by_id["graph_hybrid"])
    run_orchestrator_cases(orchestrator, cases, by_id["graph_off"])

    assert orchestrator.calls[0][1].graph_enabled is True
    assert orchestrator.calls[1][1].graph_enabled is False
    # 공정 비교: graph_enabled 외 조건은 두 변형에서 동일해야 한다.
    assert orchestrator.calls[0][1].enabled_sources == orchestrator.calls[1][1].enabled_sources
    assert orchestrator.calls[0][1].rerank_enabled == orchestrator.calls[1][1].rerank_enabled
