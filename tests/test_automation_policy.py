"""Issue #0011 자동화 정책 결정 테스트."""

from app.domain.common.enums import AutomationDecision, ChangeType, RetrievalSource
from app.domain.retrieval import CandidateLocation, RetrievalCandidate, RetrievalEvidence
from app.policy.automation import AutomationPolicyEngine, PolicyInput, PolicyThresholds


def _candidate(score=0.9, sources=(RetrievalSource.RAG, RetrievalSource.CONSTANT_MATCH), stale=False):
    evidences = tuple(
        RetrievalEvidence(source, score, score, provider_version="test-v1")
        for source in sources
    )
    return RetrievalCandidate(CandidateLocation("src/Tax.java", "calculate"), evidences, score, stale=stale)


def _input(**overrides):
    values = dict(
        change_type=ChangeType.VALUE_CHANGE,
        classification_confidence=0.95,
        candidates=(_candidate(),),
        repository_commit="abc123",
        existing_files=frozenset({"src/Tax.java"}),
        source_conflict=False,
    )
    values.update(overrides)
    return PolicyInput(**values)


def test_clear_value_change_with_two_sources_allows_draft() -> None:
    result = AutomationPolicyEngine().decide(_input())
    assert result.decision is AutomationDecision.DRAFT_ALLOWED
    assert result.block_reasons == ()
    assert result.policy_version == "automation-policy-v1"


def test_structural_change_is_analysis_only() -> None:
    result = AutomationPolicyEngine().decide(_input(change_type=ChangeType.STRUCTURAL_CHANGE))
    assert result.decision is AutomationDecision.ANALYSIS_ONLY
    assert result.block_reasons[0].code == "change_type_not_draftable"


def test_unknown_low_confidence_requires_manual_review() -> None:
    result = AutomationPolicyEngine().decide(
        _input(change_type=ChangeType.UNKNOWN, classification_confidence=0.3)
    )
    assert result.decision is AutomationDecision.MANUAL_REVIEW_REQUIRED
    assert {reason.code for reason in result.block_reasons} >= {
        "change_type_not_draftable", "classification_confidence_low"
    }


def test_stale_only_mapping_is_blocked() -> None:
    result = AutomationPolicyEngine().decide(_input(candidates=(_candidate(stale=True),)))
    assert "stale_only_evidence" in {reason.code for reason in result.block_reasons}


def test_missing_commit_file_and_source_conflict_are_all_reported() -> None:
    result = AutomationPolicyEngine().decide(
        _input(repository_commit=None, existing_files=frozenset(), source_conflict=True)
    )
    assert {reason.code for reason in result.block_reasons} >= {
        "repository_commit_missing", "candidate_file_missing", "source_conflict"
    }


def test_single_unverified_source_and_low_score_are_blocked() -> None:
    candidate = _candidate(score=0.7, sources=(RetrievalSource.RAG,))
    result = AutomationPolicyEngine().decide(_input(candidates=(candidate,)))
    assert {reason.code for reason in result.block_reasons} >= {
        "retrieval_score_low", "retrieval_evidence_insufficient"
    }


def test_valid_verified_mapping_can_replace_two_source_requirement() -> None:
    candidate = _candidate(score=1.0, sources=(RetrievalSource.VERIFIED_MAPPING,))
    result = AutomationPolicyEngine().decide(_input(candidates=(candidate,)))
    assert result.decision is AutomationDecision.DRAFT_ALLOWED


def test_configurable_threshold_is_deterministic() -> None:
    engine = AutomationPolicyEngine(PolicyThresholds(min_retrieval_score=0.95))
    first = engine.decide(_input())
    second = engine.decide(_input())
    assert first == second
    assert first.decision is AutomationDecision.MANUAL_REVIEW_REQUIRED
