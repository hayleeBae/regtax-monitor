"""#0015 매핑 검증 결정(append-only)의 순수 도메인 계약.

이름과 값은 docs/specifications/VERIFIED_MAPPING_SPEC.md §2·§3·§4·§8 및
docs/architecture/ADR.md ADR-008 과 정확히 일치해야 한다.

이 모듈은 순수 Python 만 사용한다 — FastAPI, SQLAlchemy, LLM SDK 를 import 하지
않는다(ARCHITECTURE.md 레이어 규칙). 영속화와 API 노출은 상위 계층 책임이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence


class MappingDecisionType(str, Enum):
    """결정 종류 — 스펙 §2."""

    VERIFIED = "verified"
    REJECTED = "rejected"
    STALE = "stale"
    REVOKED = "revoked"


class VerifiedReason(str, Enum):
    """검증 사유 — 스펙 §3."""

    CONFIRMED_BY_OWNER = "confirmed_by_owner"
    MATCHED_HISTORICAL_CHANGE = "matched_historical_change"
    GOLDEN_TEST_CONFIRMED = "golden_test_confirmed"
    EXACT_CONSTANT_CONFIRMED = "exact_constant_confirmed"
    DOMAIN_MAPPING_CONFIRMED = "domain_mapping_confirmed"
    OTHER = "other"


class RejectedReason(str, Enum):
    """거절 사유 — 스펙 §3."""

    WRONG_MODULE = "wrong_module"
    LEGACY_CODE = "legacy_code"
    FALSE_POSITIVE_TERM = "false_positive_term"
    SAME_VALUE_UNRELATED = "same_value_unrelated"
    GENERATED_CODE = "generated_code"
    TEST_ONLY = "test_only"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    OTHER = "other"


class StaleReason(str, Enum):
    """stale 사유 — 스펙 §3."""

    FILE_MISSING = "file_missing"
    SYMBOL_MISSING = "symbol_missing"
    CONTENT_CHANGED = "content_changed"
    MODULE_MOVED = "module_moved"
    REPOSITORY_REPLACED = "repository_replaced"


MODIFIED_BUT_VALID = "modified_but_valid"
"""hash 는 달라졌지만 symbol 이 유효한 경우의 판정값 — 스펙 §8."""


def allowed_reason_codes(decision: MappingDecisionType) -> frozenset[str]:
    """decision 타입별 허용 reason_code 값 집합.

    REVOKED 는 스펙 §3 에 사유 목록이 없어 reason_code 가 선택(None 허용)이며,
    남길 경우를 위해 일반값 `other` 만 허용한다.
    """
    if decision is MappingDecisionType.VERIFIED:
        return frozenset(reason.value for reason in VerifiedReason)
    if decision is MappingDecisionType.REJECTED:
        return frozenset(reason.value for reason in RejectedReason)
    if decision is MappingDecisionType.STALE:
        return frozenset(reason.value for reason in StaleReason)
    return frozenset({VerifiedReason.OTHER.value})


@dataclass(frozen=True)
class MappingDecisionRecord:
    """매핑 결정 1건 — 스펙 §2 필드 + §7 유효성 스냅샷.

    유효성 스냅샷(repository_commit/path_hash/symbol_hash)은 #0015 에서
    nullable best-effort 다(ADR-008). `reason_text` 에 코드 본문을 넣지 않는 것은
    호출자 책임이다 — 스펙 §7 에 따라 decision 에는 해시만 저장한다.
    """

    mapping_id: int
    decision: MappingDecisionType
    reason_code: str | None = None
    reason_text: str | None = None
    repository_commit: str | None = None
    path_hash: str | None = None
    symbol_hash: str | None = None
    actor: str = "owner"
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.mapping_id < 1:
            raise ValueError("mapping_id must be positive")
        if not isinstance(self.decision, MappingDecisionType):
            raise ValueError("decision must be a MappingDecisionType")


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _ordering_key(record: MappingDecisionRecord) -> datetime:
    """created_at 이 없으면 가장 오래된 것으로 취급한다(입력 순서 유지)."""
    created_at = record.created_at
    if created_at is None:
        return _EPOCH
    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at


def resolve_state(
    decisions: Sequence[MappingDecisionRecord],
) -> MappingDecisionType | None:
    """이력을 시간순으로 접어 최신 상태를 계산한다. 비어 있으면 None.

    스펙 §4: 마지막 이벤트가 곧 현재 상태다 —
    `VERIFIED → STALE → VERIFIED` 의 최종 상태는 VERIFIED,
    `VERIFIED → REVOKED` 는 REVOKED(직전 verified 취소). REVOKED 는 상태만
    되돌리며 후보 목록은 건드리지 않는다.
    정렬은 created_at 기준이고 동률이면 입력 순서를 유지한다(stable sort).
    """
    if not decisions:
        return None
    ordered = sorted(decisions, key=_ordering_key)
    state: MappingDecisionType | None = None
    for record in ordered:
        state = record.decision
    return state


def check_stale(
    *,
    file_exists: bool,
    symbol_present: bool,
    stored_file_hash: str | None,
    current_file_hash: str | None,
) -> str | None:
    """stale 사유(StaleReason.value) 또는 'modified_but_valid' 또는 None 을 반환.

    파일 없음 → file_missing, symbol 없음 → symbol_missing,
    hash 가 다르지만 symbol 이 유효 → 'modified_but_valid'(자동 stale 아님).
    이 함수는 판정만 하며 이벤트를 생성하지 않는다(스펙 §8).
    """
    if not file_exists:
        return StaleReason.FILE_MISSING.value
    if not symbol_present:
        return StaleReason.SYMBOL_MISSING.value
    if (
        stored_file_hash is not None
        and current_file_hash is not None
        and stored_file_hash != current_file_hash
    ):
        return MODIFIED_BUT_VALID
    return None
