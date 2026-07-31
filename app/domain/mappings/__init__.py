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

__all__ = [
    "MODIFIED_BUT_VALID",
    "MappingDecisionRecord",
    "MappingDecisionType",
    "RejectedReason",
    "StaleReason",
    "VerifiedReason",
    "allowed_reason_codes",
    "check_stale",
    "resolve_state",
]
