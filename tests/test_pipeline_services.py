"""Issue #0012 application service 통합 계약 테스트."""

from app.application.services import (
    AnalysisService,
    DbUpdateGuidance,
    MappingService,
    ProposalService,
)
from app.collector.registry import DbItem
from app.domain.changes.classification import RuleChangeClassifier
from app.domain.changes.normalization import ChangeNormalizer
from app.domain.common.enums import AutomationDecision, ChangeType, RetrievalSource
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
        ChangeNormalizer(),
        RuleChangeClassifier(),
        lambda before, after, context, amendment_text="", reason_text="": {"summary": "금액 변경", "impact": "상수 수정"},
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


def test_proposal_service_keeps_value_patch_flow_when_db_match_explicitly_none() -> None:
    # 이슈 #0025 신규 인자(db_match, guidance) 기본값 None — 기존 호출부 동작 100% 보존.
    service = ProposalService()
    policy_input = PolicyInput(ChangeType.VALUE_CHANGE, 0.95, (_candidate(),), "commit", frozenset({"src/Tax.java"}))
    result = service.propose(policy_input, lambda: {"proposal_id": 7}, db_match=None, guidance=None)
    assert result.blocked is False
    assert result.proposal == {"proposal_id": 7}


def test_proposal_service_routes_db_match_to_db_update_guidance_without_calling_generator() -> None:
    calls = []
    service = ProposalService()
    policy_input = PolicyInput(ChangeType.VALUE_CHANGE, 0.95, (_candidate(),), "commit", frozenset({"src/Tax.java"}))
    db_match = DbItem(law_id="001766", article_pattern="제129조", item_label="근로소득 간이세액표")
    guidance = DbUpdateGuidance(
        item_label="근로소득 간이세액표",
        law_name="소득세법",
        article="제129조",
        before="8%",
        after="6%",
        guidance="본 개정은 DB 데이터 갱신 대상입니다.",
    )

    result = service.propose(
        policy_input,
        lambda: calls.append(True) or {"proposal_id": 1},
        db_match=db_match,
        guidance=guidance,
    )

    assert calls == []
    assert result.blocked is True
    assert result.policy.decision is AutomationDecision.DB_UPDATE_GUIDANCE
    assert result.proposal == {
        "item_label": "근로소득 간이세액표",
        "law_name": "소득세법",
        "article": "제129조",
        "before": "8%",
        "after": "6%",
        "guidance": "본 개정은 DB 데이터 갱신 대상입니다.",
    }


def test_db_update_guidance_serialization_excludes_db_schema_literal() -> None:
    # DbItem.db_hint(담당자용 힌트, 스키마 관련 서술)는 DbUpdateGuidance에 애초에
    # 필드로 없다 — 안내 dict에는 라벨/조문/전후값/안내문구만 실린다(스펙 §6, §8).
    guidance = DbUpdateGuidance(
        item_label="4대보험요율표",
        law_name="국민건강보험법",
        article="제73조",
        before="3.545%",
        after="3.6%",
        guidance="DB에서 요율을 갱신하세요.",
    )

    result = ProposalService().propose(
        PolicyInput(ChangeType.RATE_CHANGE, 0.95, (), "commit", frozenset()),
        lambda: {"unexpected": True},
        db_match=DbItem(law_id="002261", article_pattern="제73조", item_label="4대보험요율표"),
        guidance=guidance,
    )

    assert result.proposal is not None
    assert "db_hint" not in result.proposal
    assert set(result.proposal) == {"item_label", "law_name", "article", "before", "after", "guidance"}
