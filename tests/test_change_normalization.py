"""Issue #0006 법령 변경 정규화 계약 테스트."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.domain.changes.normalization import ChangeNormalizer


def test_normalizes_money_delta_to_won() -> None:
    change = ChangeNormalizer().normalize(
        "자녀 1명당 연 15만원을 공제한다.",
        "자녀 1명당 연 25만원을 공제한다.",
    )

    assert len(change.money_changes) == 1
    delta = change.money_changes[0]
    assert (delta.before.raw, delta.before.value) == ("15만원", "150000")
    assert (delta.after.raw, delta.after.value) == ("25만원", "250000")
    assert delta.before.unit == "KRW"


def test_normalizes_bunui_rate_to_decimal_string() -> None:
    change = ChangeNormalizer().normalize(
        "세율은 100분의 6으로 한다.",
        "세율은 100분의 8로 한다.",
    )

    assert change.rate_changes[0].before.value == "0.06"
    assert change.rate_changes[0].after.value == "0.08"
    assert change.rate_changes[0].before.unit == "ratio"


def test_normalizes_korean_date_with_precision() -> None:
    change = ChangeNormalizer().normalize(
        "이 법은 2025년 1월 1일부터 시행한다.",
        "이 법은 2026년 1월 1일부터 시행한다.",
    )

    assert change.date_changes[0].before.value == "2025-01-01"
    assert change.date_changes[0].after.value == "2026-01-01"
    assert change.date_changes[0].after.precision == "day"


def test_normalizes_age_and_duration_separately() -> None:
    change = ChangeNormalizer().normalize(
        "만 18세인 사람은 2개월 이상 근무해야 한다.",
        "만 19세인 사람은 3개월 이상 근무해야 한다.",
    )

    assert change.age_changes[0].before.value == "18"
    assert change.age_changes[0].after.value == "19"
    assert change.age_changes[0].after.unit == "year"
    assert change.duration_changes[0].before.value == "2"
    assert change.duration_changes[0].after.value == "3"
    assert change.duration_changes[0].after.unit == "month"


def test_preserves_added_removed_and_comparison_signals() -> None:
    change = ChangeNormalizer().normalize(
        "소득이 7천만원 이하인 사람을 포함한다.",
        "소득이 8천만원 미만인 사람은 제외한다.",
    )

    assert "7천만원" in change.removed_text
    assert "8천만원" in change.added_text
    assert change.comparison_signals == ("이하", "미만", "포함", "제외")


def test_source_hash_is_stable_and_sensitive_to_source_text() -> None:
    normalizer = ChangeNormalizer()
    first = normalizer.normalize("15만원", "25만원")
    same = normalizer.normalize("15만원", "25만원")
    changed = normalizer.normalize("15만원", "30만원")

    assert first.source_hash == same.source_hash
    assert first.source_hash.startswith("sha256:")
    assert first.source_hash != changed.source_hash
    assert first.normalizer_version == "change-normalizer-v1"


def test_normalized_change_and_nested_values_are_immutable() -> None:
    change = ChangeNormalizer().normalize("15만원", "25만원")

    with pytest.raises(FrozenInstanceError):
        change.before_text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        change.money_changes[0].before.value = "0"  # type: ignore[misc]


def test_identical_values_do_not_create_delta() -> None:
    change = ChangeNormalizer().normalize(
        "공제액은 15만원이며 3개월 이상이다.",
        "공제액은 15만원이며 3개월 이상이다.",
    )

    assert change.money_changes == ()
    assert change.duration_changes == ()
    assert change.added_text == ()
    assert change.removed_text == ()
