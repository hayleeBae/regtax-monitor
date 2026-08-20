"""분석·검색·초안 정책을 route 밖에서 조합하는 application services."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from app.collector.registry import DbItem
from app.domain.changes.classification import ChangeClassification
from app.domain.changes.normalization import ChangeNormalizer, NormalizedChange
from app.domain.common.enums import AutomationDecision, RetrievalSource
from app.domain.retrieval import RetrievalCandidate
from app.policy.automation import AutomationPolicyEngine, PolicyInput, PolicyResult
from app.retrieval.orchestrator import RetrievalConfig, RetrievalQuery


class Classifier(Protocol):
    def classify(self, change: NormalizedChange) -> ChangeClassification: ...


@dataclass(frozen=True)
class AnalysisResult:
    normalized: NormalizedChange
    classification: ChangeClassification
    summary: str
    impact: str
    parse_ok: bool


class AnalysisService:
    def __init__(
        self,
        normalizer: ChangeNormalizer,
        classifier: Classifier,
        analyzer: Callable[..., dict],
    ) -> None:
        self.normalizer = normalizer
        self.classifier = classifier
        self.analyzer = analyzer

    def analyze(
        self,
        before: str,
        after: str,
        context: str = "",
        amendment_text: str = "",
        reason_text: str = "",
    ) -> AnalysisResult:
        # normalize/classify는 파생 발췌(before/after)만 본다 — 개정문 원문·제개정이유가
        # 값 델타 계산에 새어들면 이 이슈(#0023)가 무효가 된다(스펙 §2). amendment/reason은
        # analyzer(LLM 프롬프트)에만 컨텍스트로 전달한다.
        normalized = self.normalizer.normalize(before, after)
        classification = self.classifier.classify(normalized)
        analysis = self.analyzer(
            before, after, context, amendment_text=amendment_text, reason_text=reason_text
        )
        parse_ok = "raw" not in analysis
        return AnalysisResult(
            normalized,
            classification,
            analysis.get("summary", analysis.get("raw", "")),
            analysis.get("impact", ""),
            parse_ok,
        )


@dataclass(frozen=True)
class MappingResult:
    candidates: tuple[RetrievalCandidate, ...]
    compatibility_payload: dict


class MappingService:
    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator

    def map(
        self,
        query_text: str,
        top_k: int = 5,
        enabled_sources: frozenset[RetrievalSource] | None = None,
        article_id: str | None = None,
        change_type: str | None = None,
    ) -> MappingResult:
        config = RetrievalConfig(
            enabled_sources=enabled_sources or RetrievalConfig().enabled_sources,
            final_top_k=top_k,
        )
        response = self.orchestrator.retrieve(
            RetrievalQuery(
                query_text,
                top_k_per_provider=top_k,
                article_id=article_id,
                change_type=change_type,
            ),
            config,
        )
        return MappingResult(tuple(response.candidates), response.to_dict())


@dataclass(frozen=True)
class DbUpdateGuidance:
    """DB 데이터 개정 안내 산출물(DB_DATA_ROUTING_SPEC §6) — 실제 DB 스키마
    원문(테이블/컬럼명)은 포함하지 않는다. 일반화된 라벨과 조문 전후값만 담는다."""

    item_label: str
    law_name: str
    article: str
    before: str
    after: str
    guidance: str


@dataclass(frozen=True)
class ProposalResult:
    blocked: bool
    policy: PolicyResult
    proposal: dict | None


class ProposalService:
    def __init__(self, policy: AutomationPolicyEngine | None = None) -> None:
        self.policy = policy or AutomationPolicyEngine()

    def propose(
        self,
        policy_input: PolicyInput,
        generator: Callable[[], dict],
        db_match: DbItem | None = None,
        guidance: DbUpdateGuidance | None = None,
    ) -> ProposalResult:
        # db_match가 있으면 DbDataRegistry 정확 매칭 건이다 — 코드 draft 정책
        # 게이트·LLM 생성 경로에 진입하지 않는다(ADR-016, 스펙 §5). generator()는
        # 절대 호출하지 않는다.
        if db_match is not None:
            db_decision = PolicyResult(
                AutomationDecision.DB_UPDATE_GUIDANCE, (), self.policy.version
            )
            proposal = asdict(guidance) if guidance is not None else None
            return ProposalResult(True, db_decision, proposal)
        decision = self.policy.decide(policy_input)
        if not decision.draft_allowed:
            return ProposalResult(True, decision, None)
        return ProposalResult(False, decision, generator())

