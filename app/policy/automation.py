"""변경 유형과 검색 근거로 patch 초안 허용 여부를 결정한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.common.enums import AutomationDecision, ChangeType, RetrievalSource
from app.domain.retrieval import RetrievalCandidate


POLICY_VERSION = "automation-policy-v1"
_DRAFTABLE = {ChangeType.VALUE_CHANGE, ChangeType.RATE_CHANGE, ChangeType.DATE_CHANGE}
_ANALYSIS_ONLY = {ChangeType.STRUCTURAL_CHANGE, ChangeType.NO_CODE_IMPACT}


@dataclass(frozen=True)
class PolicyThresholds:
    min_classification_confidence: float = 0.80
    min_retrieval_score: float = 0.80
    min_independent_sources: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.min_classification_confidence <= 1:
            raise ValueError("classification threshold must be between 0 and 1")
        if not 0 <= self.min_retrieval_score <= 1:
            raise ValueError("retrieval threshold must be between 0 and 1")
        if self.min_independent_sources < 1:
            raise ValueError("min_independent_sources must be positive")


@dataclass(frozen=True)
class PolicyInput:
    change_type: ChangeType
    classification_confidence: float
    candidates: tuple[RetrievalCandidate, ...]
    repository_commit: str | None
    existing_files: frozenset[str]
    source_conflict: bool = False


@dataclass(frozen=True)
class BlockReason:
    code: str
    message: str


@dataclass(frozen=True)
class PolicyResult:
    decision: AutomationDecision
    block_reasons: tuple[BlockReason, ...]
    policy_version: str

    @property
    def draft_allowed(self) -> bool:
        return self.decision is AutomationDecision.DRAFT_ALLOWED


class AutomationPolicyEngine:
    version = POLICY_VERSION

    def __init__(self, thresholds: PolicyThresholds | None = None) -> None:
        self.thresholds = thresholds or PolicyThresholds()

    def decide(self, policy_input: PolicyInput) -> PolicyResult:
        reasons: list[BlockReason] = []
        if policy_input.change_type not in _DRAFTABLE:
            reasons.append(
                BlockReason(
                    "change_type_not_draftable",
                    f"{policy_input.change_type.value} 유형은 자동 초안 대상이 아님",
                )
            )
        if policy_input.classification_confidence < self.thresholds.min_classification_confidence:
            reasons.append(BlockReason("classification_confidence_low", "분류 신뢰도가 기준 미만"))
        if not policy_input.candidates:
            reasons.append(BlockReason("retrieval_candidate_missing", "검색 후보가 없음"))
        else:
            top = policy_input.candidates[0]
            strongest_evidence = max(
                evidence.normalized_score for evidence in top.evidences
            )
            if strongest_evidence < self.thresholds.min_retrieval_score:
                reasons.append(BlockReason("retrieval_score_low", "최상위 검색 점수가 기준 미만"))
            sources = {evidence.source for evidence in top.evidences}
            valid_verified = RetrievalSource.VERIFIED_MAPPING in sources and not top.stale
            if len(sources) < self.thresholds.min_independent_sources and not valid_verified:
                reasons.append(BlockReason("retrieval_evidence_insufficient", "독립 검색 근거가 부족함"))
            if all(candidate.stale for candidate in policy_input.candidates):
                reasons.append(BlockReason("stale_only_evidence", "현재 코드에서 유효한 검색 근거가 없음"))
            missing = sorted(
                candidate.location.path
                for candidate in policy_input.candidates
                if candidate.location.path not in policy_input.existing_files
            )
            if missing:
                reasons.append(BlockReason("candidate_file_missing", f"후보 파일이 존재하지 않음: {', '.join(missing)}"))
        if not policy_input.repository_commit:
            reasons.append(BlockReason("repository_commit_missing", "재현 가능한 repository commit이 없음"))
        if policy_input.source_conflict:
            reasons.append(BlockReason("source_conflict", "검색 provider 간 모듈 또는 대상 충돌이 있음"))

        if not reasons:
            decision = AutomationDecision.DRAFT_ALLOWED
        elif policy_input.change_type in _ANALYSIS_ONLY:
            decision = AutomationDecision.ANALYSIS_ONLY
        else:
            decision = AutomationDecision.MANUAL_REVIEW_REQUIRED
        return PolicyResult(decision, tuple(reasons), self.version)
