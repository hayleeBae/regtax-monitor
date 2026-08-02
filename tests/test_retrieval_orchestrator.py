"""Issue #0009 검색 provider 통합·실패 격리 테스트."""

from __future__ import annotations

import pytest

from app.domain.common.enums import RetrievalSource
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


def _candidate(source, path="src/Tax.java", symbol="calculate", score=0.8):
    return RetrievalCandidate(
        CandidateLocation(path, symbol, 10, 20),
        (RetrievalEvidence(source, score, score, provider_version=f"{source.value}-v1"),),
        score,
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
