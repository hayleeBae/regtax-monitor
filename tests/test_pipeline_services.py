"""Issue #0012 application service 통합 계약 테스트."""

from app.application.services import AnalysisService, MappingService, ProposalService
from app.domain.changes.classification import RuleChangeClassifier
from app.domain.changes.normalization import ChangeNormalizer
from app.domain.common.enums import ChangeType, RetrievalSource
from app.domain.retrieval import CandidateLocation, RetrievalCandidate, RetrievalEvidence
from app.policy.automation import PolicyInput


def _candidate():
    evidences = tuple(
        RetrievalEvidence(source, 0.95, 0.95, provider_version="test-v1")
        for source in (RetrievalSource.RAG, RetrievalSource.CONSTANT_MATCH)
    )
    return RetrievalCandidate(CandidateLocation("src/Tax.java", "calculate"), evidences, 0.95)


def test_analysis_service_normalizes_classifies_and_preserves_analysis() -> None:
    service = AnalysisService(
        ChangeNormalizer(), RuleChangeClassifier(), lambda before, after, context: {"summary": "금액 변경", "impact": "상수 수정"}
    )
    result = service.analyze("15만원", "25만원", "소득세법")
    assert result.classification.primary_type is ChangeType.VALUE_CHANGE
    assert result.normalized.money_changes
    assert result.summary == "금액 변경"


def test_mapping_service_returns_orchestrator_compatibility_payload() -> None:
    class FakeOrchestrator:
        def retrieve(self, query, config=None):
            class Response:
                candidates = (_candidate(),)
                def to_dict(self):
                    return {"candidates": [self.candidates[0].to_dict()], "rag_hits": [], "dict_matches": [], "const_matches": []}
            return Response()
    result = MappingService(FakeOrchestrator()).map("법령 변경", top_k=5)
    assert result.candidates[0].location.path == "src/Tax.java"
    assert "rag_hits" in result.compatibility_payload


def test_proposal_service_blocks_structural_without_calling_generator() -> None:
    calls = []
    service = ProposalService()
    policy_input = PolicyInput(ChangeType.STRUCTURAL_CHANGE, 0.95, (_candidate(),), "commit", frozenset({"src/Tax.java"}))
    result = service.propose(policy_input, lambda: calls.append(True) or {"proposal_id": 1})
    assert result.blocked is True
    assert result.proposal is None
    assert calls == []


def test_proposal_service_keeps_value_patch_flow_when_policy_passes() -> None:
    service = ProposalService()
    policy_input = PolicyInput(ChangeType.VALUE_CHANGE, 0.95, (_candidate(),), "commit", frozenset({"src/Tax.java"}))
    result = service.propose(policy_input, lambda: {"proposal_id": 7})
    assert result.blocked is False
    assert result.proposal == {"proposal_id": 7}
