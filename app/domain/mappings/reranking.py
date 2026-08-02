"""#0016 검증 이력 기반 검색 재정렬(rerank)의 순수 도메인 계약.

등급 이름과 수치는 docs/specifications/VERIFIED_MAPPING_SPEC.md §9·§10·§11,
docs/specifications/RETRIEVAL_EXPERIMENT_SPEC.md §15,
docs/architecture/ADR.md ADR-009(+보강)와 정확히 일치해야 한다.

이 모듈은 순수 Python 만 사용한다 — FastAPI, SQLAlchemy, LLM SDK 를 import 하지
않는다(ARCHITECTURE.md 레이어 규칙). DB 조회(이력 수집)와 orchestrator 배선은
상위 계층 책임이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.domain.common.enums import ChangeType
from app.domain.mappings.decisions import MappingDecisionType

RERANK_VERSION = "verified-rerank-v1"
"""rerank 규칙 버전 — `SCORING_VERSION` 과 별개로 노출한다(ADR-009)."""


class ReuseClass(str, Enum):
    """검증 이력 재사용 등급 — 스펙 §9."""

    EXACT = "exact"
    COMPATIBLE = "compatible"
    UNRELATED = "unrelated"


COMPATIBLE_CHANGE_TYPES: tuple[frozenset[ChangeType], ...] = (
    frozenset(
        {
            ChangeType.VALUE_CHANGE,
            ChangeType.RATE_CHANGE,
            ChangeType.TABLE_CHANGE,
        }
    ),
    frozenset({ChangeType.CONDITION_CHANGE, ChangeType.NEW_FIELD}),
)
"""§9-2 "같은 조문 + 호환 type" 그룹.

`ChangeType.UNKNOWN`·`ChangeType.NO_CODE_IMPACT` 는 어느 그룹에도 넣지 않는다 —
미분류·무영향 건이 검증 매핑을 끌어올리면 오탐이 된다.
"""

VERIFIED_EXACT_BOOST = 0.35
"""§10 "valid exact verified +0.35"."""

VERIFIED_COMPATIBLE_BOOST = 0.20
"""§10 "compatible verified +0.20"."""

GOLDEN_CONFIRMED_BOOST = 0.05
"""§10 "golden-confirmed +0.05" — verified boost 에 가산."""

HISTORICAL_MATCH_BOOST = 0.05
"""§10 "historical match +0.05" — verified boost 에 가산."""

REJECTED_EXACT_PENALTY = -0.30
"""§10 "rejected exact -0.30"."""

REPEATED_REJECTION_THRESHOLD = 2
"""§11 반복 거절로 판단하는 누적 REJECTED 이벤트 수."""

REPEATED_REJECTION_PENALTY = -0.50
"""§10 "repeated rejection 최대 -0.50" — EXACT 문맥에서만 적용한다."""

STALE_PENALTY = -0.50
"""§10 "stale boost 제거 + penalty".

merge 단계(`_merge_candidates`)가 이미 -0.50 을 적용했다면 다시 얹지 않는다 —
총 stale penalty 를 -0.50 으로 cap 하는 것이 ADR-009 보강 3항의 결정이다.
"""

LEGACY_PENALTY = -0.15
"""§10 "legacy -0.15" — #0015 backfill 이력은 사람이 확인한 근거가 아니다."""

DELTA_MIN = -0.50
DELTA_MAX = 0.45
"""최종 delta clamp 범위.

단일 신호가 다른 근거를 압도하면 RETRIEVAL_EXPERIMENT_SPEC §15 의
"검증 이력만으로 다른 exact evidence 를 제거하지 않는다"를 위반한다.
"""


@dataclass(frozen=True)
class DecisionContext:
    """후보 위치 1건에 붙은 결정 이력 요약.

    `state` 는 `resolve_state()` 결과를 그대로 담는다(이력이 없으면 None) —
    #0015 의 `MappingDecisionType` 어휘를 재사용하며 새 상태 enum 을 만들지 않는다.
    `change_type` 은 레거시 자유 문자열(rate/limit/date/formula/logic)일 수 있다.
    """

    article_id: str | None
    change_type: str | None
    state: MappingDecisionType | None
    reason_code: str | None = None
    rejection_count: int = 0
    golden_confirmed: bool = False
    historical_match: bool = False
    legacy: bool = False

    def __post_init__(self) -> None:
        if self.rejection_count < 0:
            raise ValueError("rejection_count must not be negative")
        if self.state is not None and not isinstance(
            self.state, MappingDecisionType
        ):
            raise ValueError("state must be a MappingDecisionType or None")


def _parse_change_type(value: str) -> ChangeType | None:
    """V2 어휘로 파싱되면 ChangeType, 레거시 문자열이면 None."""
    try:
        return ChangeType(value)
    except ValueError:
        return None


def classify_reuse(
    query_article_id: str | None,
    query_change_type: str | None,
    context: DecisionContext,
) -> ReuseClass:
    """쿼리 문맥과 이력 문맥을 대조해 재사용 등급을 판정한다 — 스펙 §9.

    조문이 다르거나 없으면 언제나 UNRELATED 다 — §9 "문맥이 다르면 boost하지
    않는다", §11 "다른 법령에서의 거절을 영구 차단으로 쓰지 않는다".
    change_type 비교는 보수적이다: 한쪽이라도 V2 `ChangeType` 으로 파싱되지 않으면
    문자열 완전일치일 때만 EXACT 로 본다(레거시→V2 임의 변환 금지 — ADR-009).
    두 값이 모두 비어 있으면 무문맥 boost 금지(ADR-009 보강 1항)로 UNRELATED.
    """
    if not query_article_id or not context.article_id:
        return ReuseClass.UNRELATED
    if query_article_id != context.article_id:
        return ReuseClass.UNRELATED

    query_type = (query_change_type or "").strip()
    context_type = (context.change_type or "").strip()
    if not query_type or not context_type:
        return ReuseClass.UNRELATED

    parsed_query = _parse_change_type(query_type)
    parsed_context = _parse_change_type(context_type)
    if parsed_query is None or parsed_context is None:
        return (
            ReuseClass.EXACT
            if query_type == context_type
            else ReuseClass.UNRELATED
        )

    if parsed_query is parsed_context:
        return ReuseClass.EXACT
    for group in COMPATIBLE_CHANGE_TYPES:
        if parsed_query in group and parsed_context in group:
            return ReuseClass.COMPATIBLE
    return ReuseClass.UNRELATED


def rerank_delta(
    contexts: Sequence[DecisionContext],
    *,
    query_article_id: str | None,
    query_change_type: str | None,
    merge_stale_applied: bool = False,
) -> float:
    """후보 위치 1건의 이력 전체를 접어 점수 delta(음수 가능)를 계산한다 — §10.

    UNRELATED 로 게이팅된 문맥은 기여하지 않는다(delta 0). 상태별 규칙:

    - VERIFIED: EXACT +0.35 / COMPATIBLE +0.20, golden·historical 각 +0.05 가산,
      legacy 이력이면 -0.15.
    - REJECTED: EXACT 문맥에서만 -0.30. 누적 거절이 `REPEATED_REJECTION_THRESHOLD`
      이상이면 총 거절 penalty 를 -0.50 까지 강화한다(§11).
    - STALE: verified boost 를 주지 않는다. 추가 penalty 는 `merge_stale_applied`
      가 False 일 때만 적용한다(ADR-009 보강 3항 — 총 -0.50 cap).
    - REVOKED / 이력 없음: boost·penalty 모두 없음(취소된 검증).

    최종값은 `[DELTA_MIN, DELTA_MAX]` 로 clamp 하고 부동소수 오차를 없애기 위해
    소수점 6자리로 반올림한다.
    """
    delta = 0.0
    rejection_penalty = 0.0
    max_rejection_count = 0
    has_stale = False

    for context in contexts:
        reuse = classify_reuse(query_article_id, query_change_type, context)
        if reuse is ReuseClass.UNRELATED:
            continue

        state = context.state
        if state is MappingDecisionType.VERIFIED:
            boost = (
                VERIFIED_EXACT_BOOST
                if reuse is ReuseClass.EXACT
                else VERIFIED_COMPATIBLE_BOOST
            )
            if context.golden_confirmed:
                boost += GOLDEN_CONFIRMED_BOOST
            if context.historical_match:
                boost += HISTORICAL_MATCH_BOOST
            if context.legacy:
                boost += LEGACY_PENALTY
            delta += boost
        elif state is MappingDecisionType.REJECTED:
            if reuse is ReuseClass.EXACT:
                rejection_penalty += REJECTED_EXACT_PENALTY
                max_rejection_count = max(
                    max_rejection_count, context.rejection_count
                )
        elif state is MappingDecisionType.STALE:
            has_stale = True

    if max_rejection_count >= REPEATED_REJECTION_THRESHOLD:
        rejection_penalty = min(rejection_penalty, REPEATED_REJECTION_PENALTY)
    delta += rejection_penalty

    if has_stale and not merge_stale_applied:
        delta += STALE_PENALTY

    return round(max(DELTA_MIN, min(DELTA_MAX, delta)), 6)
