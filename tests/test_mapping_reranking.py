"""Issue #0016 검증 이력 rerank 도메인 계약 테스트."""

from __future__ import annotations

import pytest

from app.domain.common.enums import ChangeType
from app.domain.mappings import (
    COMPATIBLE_CHANGE_TYPES,
    RERANK_VERSION,
    DecisionContext,
    MappingDecisionType,
    ReuseClass,
    classify_reuse,
    rerank_delta,
)

_ARTICLE = "소득세법-제59조의4"
_OTHER_ARTICLE = "소득세법-제52조"


def _context(
    *,
    article_id: str | None = _ARTICLE,
    change_type: str | None = ChangeType.VALUE_CHANGE.value,
    state: MappingDecisionType | None = MappingDecisionType.VERIFIED,
    **kwargs: object,
) -> DecisionContext:
    return DecisionContext(
        article_id=article_id,
        change_type=change_type,
        state=state,
        **kwargs,  # type: ignore[arg-type]
    )


def _delta(
    *contexts: DecisionContext,
    change_type: str | None = ChangeType.VALUE_CHANGE.value,
    merge_stale_applied: bool = False,
) -> float:
    return rerank_delta(
        contexts,
        query_article_id=_ARTICLE,
        query_change_type=change_type,
        merge_stale_applied=merge_stale_applied,
    )


# --- classify_reuse (스펙 §9) -------------------------------------------------


def test_same_article_same_v2_type_is_exact() -> None:
    reuse = classify_reuse(
        _ARTICLE, ChangeType.RATE_CHANGE.value,
        _context(change_type=ChangeType.RATE_CHANGE.value),
    )
    assert reuse is ReuseClass.EXACT


def test_same_article_compatible_type_is_compatible() -> None:
    reuse = classify_reuse(
        _ARTICLE, ChangeType.VALUE_CHANGE.value,
        _context(change_type=ChangeType.RATE_CHANGE.value),
    )
    assert reuse is ReuseClass.COMPATIBLE


def test_same_article_incompatible_type_is_unrelated() -> None:
    reuse = classify_reuse(
        _ARTICLE, ChangeType.VALUE_CHANGE.value,
        _context(change_type=ChangeType.DATE_CHANGE.value),
    )
    assert reuse is ReuseClass.UNRELATED


def test_different_article_is_unrelated_even_with_same_type() -> None:
    reuse = classify_reuse(
        _ARTICLE, ChangeType.VALUE_CHANGE.value,
        _context(article_id=_OTHER_ARTICLE),
    )
    assert reuse is ReuseClass.UNRELATED


def test_missing_query_article_is_unrelated() -> None:
    assert (
        classify_reuse(None, ChangeType.VALUE_CHANGE.value, _context())
        is ReuseClass.UNRELATED
    )


def test_missing_context_article_is_unrelated() -> None:
    reuse = classify_reuse(
        _ARTICLE, ChangeType.VALUE_CHANGE.value, _context(article_id=None)
    )
    assert reuse is ReuseClass.UNRELATED


def test_missing_change_types_are_unrelated() -> None:
    reuse = classify_reuse(_ARTICLE, None, _context(change_type=None))
    assert reuse is ReuseClass.UNRELATED


def test_legacy_change_type_exact_string_match_is_exact() -> None:
    reuse = classify_reuse(_ARTICLE, "rate", _context(change_type="rate"))
    assert reuse is ReuseClass.EXACT


def test_legacy_change_type_is_not_translated_to_v2() -> None:
    """레거시 'rate' 와 V2 'rate_change' 를 같은 것으로 보지 않는다."""
    assert (
        classify_reuse(_ARTICLE, "rate", _context(change_type="rate_change"))
        is ReuseClass.UNRELATED
    )
    assert (
        classify_reuse(_ARTICLE, "rate_change", _context(change_type="rate"))
        is ReuseClass.UNRELATED
    )


def test_legacy_change_types_differing_are_unrelated() -> None:
    reuse = classify_reuse(_ARTICLE, "formula", _context(change_type="logic"))
    assert reuse is ReuseClass.UNRELATED


def test_unknown_and_no_code_impact_are_not_in_compatible_groups() -> None:
    excluded = {ChangeType.UNKNOWN, ChangeType.NO_CODE_IMPACT}
    for group in COMPATIBLE_CHANGE_TYPES:
        assert not (group & excluded)


# --- rerank_delta (스펙 §10·§11) ---------------------------------------------


def test_empty_contexts_yield_zero_delta() -> None:
    assert _delta() == 0.0


def test_exact_verified_boost() -> None:
    assert _delta(_context()) == pytest.approx(0.35)


def test_compatible_verified_boost() -> None:
    delta = _delta(_context(change_type=ChangeType.TABLE_CHANGE.value))
    assert delta == pytest.approx(0.20)


def test_golden_and_historical_add_to_verified_boost() -> None:
    assert _delta(_context(golden_confirmed=True)) == pytest.approx(0.40)
    assert _delta(_context(historical_match=True)) == pytest.approx(0.40)
    both = _delta(_context(golden_confirmed=True, historical_match=True))
    assert both == pytest.approx(0.45)


def test_legacy_verified_boost_is_reduced() -> None:
    assert _delta(_context(legacy=True)) == pytest.approx(0.20)


def test_exact_rejected_penalty() -> None:
    delta = _delta(_context(state=MappingDecisionType.REJECTED))
    assert delta == pytest.approx(-0.30)


def test_repeated_rejection_strengthens_penalty() -> None:
    delta = _delta(
        _context(state=MappingDecisionType.REJECTED, rejection_count=3)
    )
    assert delta == pytest.approx(-0.50)


def test_unrelated_context_contributes_nothing() -> None:
    assert _delta(_context(article_id=_OTHER_ARTICLE)) == 0.0
    assert (
        _delta(
            _context(
                article_id=_OTHER_ARTICLE,
                state=MappingDecisionType.REJECTED,
                rejection_count=5,
            )
        )
        == 0.0
    )


def test_stale_removes_boost_and_applies_penalty_when_merge_did_not() -> None:
    delta = _delta(
        _context(state=MappingDecisionType.STALE, golden_confirmed=True),
        merge_stale_applied=False,
    )
    assert delta == pytest.approx(-0.50)


def test_stale_penalty_is_not_double_counted_after_merge() -> None:
    delta = _delta(
        _context(state=MappingDecisionType.STALE, golden_confirmed=True),
        merge_stale_applied=True,
    )
    assert delta == 0.0


def test_revoked_gives_neither_boost_nor_penalty() -> None:
    delta = _delta(
        _context(state=MappingDecisionType.REVOKED, golden_confirmed=True)
    )
    assert delta == 0.0


def test_no_history_state_gives_zero() -> None:
    assert _delta(_context(state=None)) == 0.0


def test_delta_is_clamped_at_upper_bound() -> None:
    delta = _delta(
        _context(golden_confirmed=True, historical_match=True),
        _context(change_type=ChangeType.RATE_CHANGE.value),
        _context(),
    )
    assert delta == pytest.approx(0.45)


def test_delta_is_clamped_at_lower_bound() -> None:
    delta = _delta(
        _context(state=MappingDecisionType.REJECTED, rejection_count=4),
        _context(state=MappingDecisionType.STALE),
    )
    assert delta == pytest.approx(-0.50)


def test_rerank_version_is_stable() -> None:
    assert RERANK_VERSION == "verified-rerank-v1"


def test_negative_rejection_count_is_rejected() -> None:
    with pytest.raises(ValueError):
        DecisionContext(
            article_id=_ARTICLE,
            change_type=ChangeType.VALUE_CHANGE.value,
            state=MappingDecisionType.REJECTED,
            rejection_count=-1,
        )
