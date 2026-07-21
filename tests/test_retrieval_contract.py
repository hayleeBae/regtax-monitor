"""Issue #0008 공통 검색 후보 계약 테스트."""

from __future__ import annotations

import math

import pytest

from app.domain.common.enums import RetrievalSource
from app.domain.retrieval.candidate import (
    CandidateLocation,
    IdentityScoreNormalizer,
    RetrievalCandidate,
    RetrievalEvidence,
)


def _candidate(source: RetrievalSource, raw_score: float) -> RetrievalCandidate:
    evidence = RetrievalEvidence(
        source=source,
        raw_score=raw_score,
        normalized_score=min(raw_score, 1.0),
        matched_terms=("자녀세액공제",),
        matched_values=("150000",),
        explanation="법령 용어와 코드 값이 일치함",
        provider_version="test-v1",
        raw_payload={"internal_query": "secret-debug", "original": raw_score},
    )
    return RetrievalCandidate(
        location=CandidateLocation("src/tax/TaxService.java", "calculate", 10, 20),
        evidences=(evidence,),
        final_score=evidence.normalized_score,
    )


def test_rag_dictionary_constant_share_one_json_shape() -> None:
    candidates = [
        _candidate(RetrievalSource.RAG, 0.82),
        _candidate(RetrievalSource.TERM_DICTIONARY, 0.91),
        _candidate(RetrievalSource.CONSTANT_MATCH, 1.0),
    ]

    public = [candidate.to_dict() for candidate in candidates]
    assert {tuple(item.keys()) for item in public} == {tuple(public[0].keys())}
    assert [item["evidences"][0]["source"] for item in public] == [
        "rag",
        "term_dictionary",
        "constant_match",
    ]
    assert all("raw_payload" not in item["evidences"][0] for item in public)


def test_debug_serialization_preserves_provider_raw_score_and_payload() -> None:
    candidate = _candidate(RetrievalSource.RAG, 0.82)
    debug = candidate.to_dict(include_debug=True)

    assert debug["evidences"][0]["raw_score"] == 0.82
    assert debug["evidences"][0]["raw_payload"]["original"] == 0.82
    assert candidate.evidences[0].raw_score == 0.82


def test_dedup_key_uses_normalized_path_and_symbol() -> None:
    first = CandidateLocation("./src/tax/../tax/TaxService.java", "calculate", 10, 20)
    same = CandidateLocation("src/tax/TaxService.java", "calculate", 100, 120)
    other_symbol = CandidateLocation("src/tax/TaxService.java", "withholding", 10, 20)

    assert first.dedup_key == same.dedup_key
    assert first.dedup_key != other_symbol.dedup_key


def test_dedup_key_without_symbol_uses_stable_line_bucket() -> None:
    first = CandidateLocation("src/A.java", None, 12, 15)
    same_bucket = CandidateLocation("src/A.java", None, 19, 21)
    next_bucket = CandidateLocation("src/A.java", None, 20, 25)

    assert first.dedup_key == same_bucket.dedup_key
    assert first.dedup_key != next_bucket.dedup_key


def test_stale_and_verified_state_are_serialized() -> None:
    base = _candidate(RetrievalSource.VERIFIED_MAPPING, 1.0)
    candidate = RetrievalCandidate(
        location=base.location,
        evidences=base.evidences,
        final_score=0.0,
        verified_state="approved",
        stale=True,
    )

    assert candidate.to_dict()["stale"] is True
    assert candidate.to_dict()["verified_state"] == "approved"


@pytest.mark.parametrize("score", [-0.1, 1.1, math.nan, math.inf])
def test_normalized_score_rejects_invalid_values(score: float) -> None:
    with pytest.raises(ValueError):
        RetrievalEvidence(
            source=RetrievalSource.RAG,
            raw_score=0.5,
            normalized_score=score,
            provider_version="rag-v1",
        )


def test_identity_normalizer_records_version_and_rejects_out_of_range() -> None:
    normalizer = IdentityScoreNormalizer()

    assert normalizer.version == "identity-normalizer-v1"
    assert normalizer.normalize(RetrievalSource.RAG, 0.75, {}) == 0.75
    with pytest.raises(ValueError):
        normalizer.normalize(RetrievalSource.RAG, 2.0, {})
