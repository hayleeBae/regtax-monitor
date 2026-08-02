"""매핑 검증 결정의 순수 도메인 계약."""

from app.domain.mappings.decisions import (
    MODIFIED_BUT_VALID,
    MappingDecisionRecord,
    MappingDecisionType,
    RejectedReason,
    StaleReason,
    VerifiedReason,
    allowed_reason_codes,
    check_stale,
    resolve_state,
)
from app.domain.mappings.reranking import (
    COMPATIBLE_CHANGE_TYPES,
    RERANK_VERSION,
    DecisionContext,
    ReuseClass,
    classify_reuse,
    rerank_delta,
)

__all__ = [
    "COMPATIBLE_CHANGE_TYPES",
    "MODIFIED_BUT_VALID",
    "RERANK_VERSION",
    "DecisionContext",
    "MappingDecisionRecord",
    "MappingDecisionType",
    "RejectedReason",
    "ReuseClass",
    "StaleReason",
    "VerifiedReason",
    "allowed_reason_codes",
    "check_stale",
    "classify_reuse",
    "rerank_delta",
    "resolve_state",
]
