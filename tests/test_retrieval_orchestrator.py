"""Issue #0009 검색 provider 통합·실패 격리 테스트."""

from __future__ import annotations

import pytest

from app.domain.common.enums import RetrievalSource
from app.domain.mappings.decisions import MappingDecisionType
from app.domain.mappings.reranking import DecisionContext
from app.domain.retrieval import CandidateLocation, RetrievalCandidate, RetrievalEvidence
from app.retrieval.orchestrator import (
    ProviderResult,
    RetrievalConfig,
    RetrievalError,
    RetrievalOrchestrator,
    RetrievalQuery,
)


class _Provider:
    def __init__(self, source, candidates=(), error=None):
        self.source = source
        self.version = f"{source.value}-v1"
        self.candidates = tuple(candidates)
        self.error = error
        self.calls = 0

    def retrieve(self, query):
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderResult(self.source, self.candidates, (), 1)


def _candidate(source, path="src/Tax.java", symbol="calculate", score=0.8, stale=False):
    return RetrievalCandidate(
        CandidateLocation(path, symbol, 10, 20),
        (RetrievalEvidence(source, score, score, provider_version=f"{source.value}-v1"),),
        score,
        stale=stale,
    )


def test_merges_duplicate_candidates_and_keeps_source_scores() -> None:
    rag = _Provider(RetrievalSource.RAG, [_candidate(RetrievalSource.RAG, score=0.8)])
    const = _Provider(
        RetrievalSource.CONSTANT_MATCH,
        [_candidate(RetrievalSource.CONSTANT_MATCH, score=1.0)],
    )
    response = RetrievalOrchestrator([rag, const]).retrieve(RetrievalQuery("query"))

    assert len(response.candidates) == 1
    assert {e.source for e in response.candidates[0].evidences} == {
        RetrievalSource.RAG,
        RetrievalSource.CONSTANT_MATCH,
    }
    assert [e.raw_score for e in response.candidates[0].evidences] == [1.0, 0.8]
    assert response.candidates[0].rank == 1


def test_provider_failure_is_isolated_and_reported() -> None:
    rag = _Provider(RetrievalSource.RAG, error=RuntimeError("index unavailable"))
    dictionary = _Provider(
        RetrievalSource.TERM_DICTIONARY,
        [_candidate(RetrievalSource.TERM_DICTIONARY)],
    )
    response = RetrievalOrchestrator([rag, dictionary]).retrieve(RetrievalQuery("query"))

    assert len(response.candidates) == 1
    assert response.provider_statuses["rag"].status == "error"
    assert "index unavailable" in response.provider_statuses["rag"].message
    assert response.provider_statuses["term_dictionary"].status == "success"


def test_all_enabled_provider_failures_raise_retrieval_error() -> None:
    providers = [
        _Provider(RetrievalSource.RAG, error=RuntimeError("down")),
        _Provider(RetrievalSource.CONSTANT_MATCH, error=RuntimeError("broken")),
    ]
    with pytest.raises(RetrievalError, match="RETRIEVAL_ERROR"):
        RetrievalOrchestrator(providers).retrieve(RetrievalQuery("query"))


def test_feature_flags_skip_disabled_provider() -> None:
    rag = _Provider(RetrievalSource.RAG, [_candidate(RetrievalSource.RAG)])
    const = _Provider(RetrievalSource.CONSTANT_MATCH, [_candidate(RetrievalSource.CONSTANT_MATCH)])
    config = RetrievalConfig(enabled_sources=frozenset({RetrievalSource.RAG}))
    response = RetrievalOrchestrator([rag, const]).retrieve(RetrievalQuery("query"), config)

    assert rag.calls == 1
    assert const.calls == 0
    assert response.provider_statuses["constant_match"].status == "disabled"


def test_query_context_fields_default_to_none_for_positional_callers() -> None:
    query = RetrievalQuery("query")

    assert query.article_id is None
    assert query.change_type is None
    assert query.domain is None
    assert query.top_k_per_provider == 8


def test_query_context_does_not_change_response_or_query_hash() -> None:
    """#0016 step 1은 통로만 넓힌다 — 문맥 유무로 결과가 달라지면 안 된다."""

    def _run(query):
        rag = _Provider(
            RetrievalSource.RAG,
            [
                _candidate(RetrievalSource.RAG, "src/A.java", score=0.9),
                _candidate(RetrievalSource.RAG, "src/B.java", score=0.7),
            ],
        )
        const = _Provider(
            RetrievalSource.CONSTANT_MATCH,
            [_candidate(RetrievalSource.CONSTANT_MATCH, "src/A.java", score=1.0)],
        )
        return RetrievalOrchestrator([rag, const]).retrieve(query)

    plain = _run(RetrievalQuery("query"))
    contextual = _run(
        RetrievalQuery("query", article_id="법령001:제59조의4", change_type="limit")
    )

    assert contextual.query_hash == plain.query_hash
    assert contextual.candidates == plain.candidates
    assert contextual.scoring_version == plain.scoring_version
    assert contextual.warnings == plain.warnings
    # 응답 계약(키 집합)도 그대로다 — duration만 측정마다 달라진다.
    plain_payload = plain.to_dict()
    contextual_payload = contextual.to_dict()
    assert contextual_payload.keys() == plain_payload.keys()
    for key in plain_payload:
        if key in ("duration_ms", "provider_statuses"):
            continue
        assert contextual_payload[key] == plain_payload[key]
    assert contextual_payload["provider_statuses"].keys() == (
        plain_payload["provider_statuses"].keys()
    )


def test_top_k_ranking_and_compatibility_views() -> None:
    rag = _Provider(
        RetrievalSource.RAG,
        [
            _candidate(RetrievalSource.RAG, "src/A.java", score=0.9),
            _candidate(RetrievalSource.RAG, "src/B.java", score=0.7),
        ],
    )
    response = RetrievalOrchestrator([rag]).retrieve(
        RetrievalQuery("query"), RetrievalConfig(final_top_k=1)
    )
    payload = response.to_dict()

    assert len(response.candidates) == 1
    assert payload["rag_hits"][0]["path"] == "src/A.java"
    assert payload["dict_matches"] == []
    assert payload["const_matches"] == []
    assert payload["scoring_version"] == "retrieval-scoring-v1"


# --- #0016 step 2: 후처리 rerank 단계 -------------------------------------


class _Reranker:
    """이력 조회(Step 3)를 대신하는 가짜 구현 — 인자와 호출 횟수를 캡처한다."""

    version = "fake-rerank-v1"

    def __init__(self, contexts=None, error=None):
        self.contexts = dict(contexts or {})
        self.error = error
        self.calls = []

    def contexts_for(self, query, candidates):
        self.calls.append((query, tuple(candidates)))
        if self.error:
            raise self.error
        return self.contexts


def _dedup_key(path, symbol="calculate"):
    return CandidateLocation(path, symbol, 10, 20).dedup_key


def _rag_provider(*paths_and_scores):
    return _Provider(
        RetrievalSource.RAG,
        [
            _candidate(RetrievalSource.RAG, path, score=score)
            for path, score in paths_and_scores
        ],
    )


def _verified_context(change_type="rate_change", **kwargs):
    return DecisionContext(
        article_id="법령001:제59조의4",
        change_type=change_type,
        state=MappingDecisionType.VERIFIED,
        **kwargs,
    )


_CONTEXT_QUERY = RetrievalQuery(
    "query", article_id="법령001:제59조의4", change_type="rate_change"
)


def test_rerank_is_skipped_when_no_reranker_is_injected() -> None:
    provider = _rag_provider(("src/A.java", 0.9), ("src/B.java", 0.7))
    response = RetrievalOrchestrator([provider]).retrieve(_CONTEXT_QUERY)
    payload = response.to_dict()

    assert response.rerank_version is None
    assert payload["rerank_version"] is None
    assert [c.location.path for c in response.candidates] == ["src/A.java", "src/B.java"]
    assert payload["scoring_version"] == "retrieval-scoring-v1"


def test_rerank_disabled_flag_does_not_call_reranker() -> None:
    contexts = {_dedup_key("src/B.java"): (_verified_context(),)}
    reranker = _Reranker(contexts)
    baseline = RetrievalOrchestrator(
        [_rag_provider(("src/A.java", 0.9), ("src/B.java", 0.7))]
    ).retrieve(_CONTEXT_QUERY)
    response = RetrievalOrchestrator(
        [_rag_provider(("src/A.java", 0.9), ("src/B.java", 0.7))], reranker
    ).retrieve(_CONTEXT_QUERY, RetrievalConfig(rerank_enabled=False))

    assert reranker.calls == []
    assert response.candidates == baseline.candidates
    assert response.rerank_version is None
    assert response.to_dict()["rerank_version"] is None


def test_rerank_runs_before_final_top_k_truncation() -> None:
    """절단 뒤에 rerank 하면 상위 K 밖의 검증 후보가 올라올 수 없다(ADR-009 보강 2항)."""
    provider = _rag_provider(
        ("src/A.java", 0.9),
        ("src/B.java", 0.8),
        ("src/C.java", 0.7),
        ("src/D.java", 0.6),
        ("src/E.java", 0.5),
    )
    reranker = _Reranker({_dedup_key("src/E.java"): (_verified_context(),)})
    baseline = RetrievalOrchestrator(
        [
            _rag_provider(
                ("src/A.java", 0.9),
                ("src/B.java", 0.8),
                ("src/C.java", 0.7),
                ("src/D.java", 0.6),
                ("src/E.java", 0.5),
            )
        ]
    ).retrieve(_CONTEXT_QUERY, RetrievalConfig(final_top_k=3))

    assert [c.location.path for c in baseline.candidates] == [
        "src/A.java",
        "src/B.java",
        "src/C.java",
    ]

    response = RetrievalOrchestrator([provider], reranker).retrieve(
        _CONTEXT_QUERY, RetrievalConfig(final_top_k=3)
    )

    # reranker 는 절단 전 후보 전체(5건)를 본다.
    assert len(reranker.calls[0][1]) == 5
    assert response.candidates[0].location.path == "src/E.java"
    assert [c.location.path for c in response.candidates] == [
        "src/E.java",
        "src/A.java",
        "src/B.java",
    ]
    assert response.rerank_version == "fake-rerank-v1"
    assert response.to_dict()["rerank_version"] == "fake-rerank-v1"


def test_rerank_keeps_scores_in_range_and_ranks_contiguous() -> None:
    provider = _rag_provider(
        ("src/A.java", 1.0), ("src/B.java", 0.9), ("src/C.java", 0.1)
    )
    reranker = _Reranker(
        {
            # 상한을 넘기려는 boost 와 하한을 넘기려는 penalty 를 동시에 건다.
            _dedup_key("src/A.java"): (
                _verified_context(golden_confirmed=True, historical_match=True),
            ),
            _dedup_key("src/C.java"): (
                DecisionContext(
                    article_id="법령001:제59조의4",
                    change_type="rate_change",
                    state=MappingDecisionType.REJECTED,
                    rejection_count=3,
                ),
            ),
        }
    )
    response = RetrievalOrchestrator([provider], reranker).retrieve(
        _CONTEXT_QUERY, RetrievalConfig(final_top_k=10)
    )

    assert [c.rank for c in response.candidates] == [1, 2, 3]
    for candidate in response.candidates:
        assert 0.0 <= candidate.final_score <= 1.0
    scores = [c.final_score for c in response.candidates]
    assert scores == sorted(scores, reverse=True)


def test_rerank_failure_is_isolated_and_reported_as_warning() -> None:
    provider = _rag_provider(("src/A.java", 0.9), ("src/B.java", 0.7))
    baseline = RetrievalOrchestrator(
        [_rag_provider(("src/A.java", 0.9), ("src/B.java", 0.7))]
    ).retrieve(_CONTEXT_QUERY)
    reranker = _Reranker(error=RuntimeError("verified db down"))
    response = RetrievalOrchestrator([provider], reranker).retrieve(_CONTEXT_QUERY)

    assert response.candidates == baseline.candidates
    assert response.rerank_version is None
    assert any(warning.startswith("rerank: ") for warning in response.warnings)
    assert "verified db down" in " ".join(response.warnings)


def test_rerank_receives_merge_stale_applied_for_stale_candidates(monkeypatch) -> None:
    """merge 가 이미 -0.50 을 적용했음을 rerank 에 알린다(ADR-009 보강 3항)."""
    from app.retrieval import orchestrator as orchestrator_module

    captured = []

    def _spy(contexts, **kwargs):
        captured.append(kwargs)
        return 0.0

    monkeypatch.setattr(orchestrator_module, "rerank_delta", _spy)

    provider = _Provider(
        RetrievalSource.RAG,
        [
            _candidate(RetrievalSource.RAG, "src/A.java", score=0.9),
            _candidate(RetrievalSource.RAG, "src/B.java", score=0.7, stale=True),
        ],
    )
    contexts = {
        _dedup_key("src/A.java"): (_verified_context(),),
        _dedup_key("src/B.java"): (
            DecisionContext(
                article_id="법령001:제59조의4",
                change_type="rate_change",
                state=MappingDecisionType.STALE,
            ),
        ),
    }
    RetrievalOrchestrator([provider], _Reranker(contexts)).retrieve(_CONTEXT_QUERY)

    flags = [call["merge_stale_applied"] for call in captured]
    assert flags == [False, True]
    assert {call["query_article_id"] for call in captured} == {"법령001:제59조의4"}
    assert {call["query_change_type"] for call in captured} == {"rate_change"}
