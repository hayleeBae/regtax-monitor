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


class _CapturingOrchestrator:
    """넘어온 RetrievalQuery를 그대로 붙잡아 두는 가짜 orchestrator."""

    def __init__(self) -> None:
        self.queries = []

    def retrieve(self, query, config=None):
        self.queries.append(query)

        class Response:
            candidates = ()

            def to_dict(self):
                return {"candidates": []}

        return Response()


def test_mapping_service_map_works_without_context_arguments() -> None:
    orchestrator = _CapturingOrchestrator()
    MappingService(orchestrator).map("법령 변경")

    query = orchestrator.queries[0]
    assert query.article_id is None
    assert query.change_type is None


def test_mapping_service_forwards_query_context_to_orchestrator() -> None:
    orchestrator = _CapturingOrchestrator()
    MappingService(orchestrator).map(
        "법령 변경", top_k=5, article_id="법령001:제59조의4", change_type="limit"
    )

    query = orchestrator.queries[0]
    assert query.text == "법령 변경"
    assert query.article_id == "법령001:제59조의4"
    # 레거시 자유 문자열은 변환 없이 그대로 전달된다.
    assert query.change_type == "limit"
    assert query.top_k_per_provider == 5


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
