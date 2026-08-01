"""Issue #0015 매핑 결정 도메인 계약 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.mappings import (
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

_BASE = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


def _decision(
    decision: MappingDecisionType, minutes: int
) -> MappingDecisionRecord:
    return MappingDecisionRecord(
        mapping_id=11,
        decision=decision,
        created_at=_BASE + timedelta(minutes=minutes),
    )


def test_empty_history_has_no_state() -> None:
    assert resolve_state([]) is None


def test_single_verified_resolves_to_verified() -> None:
    state = resolve_state([_decision(MappingDecisionType.VERIFIED, 0)])
    assert state is MappingDecisionType.VERIFIED


def test_verified_stale_verified_resolves_to_verified() -> None:
    history = [
        _decision(MappingDecisionType.VERIFIED, 0),
        _decision(MappingDecisionType.STALE, 5),
        _decision(MappingDecisionType.VERIFIED, 10),
    ]
    assert resolve_state(history) is MappingDecisionType.VERIFIED


def test_revoke_cancels_previous_verified() -> None:
    history = [
        _decision(MappingDecisionType.VERIFIED, 0),
        _decision(MappingDecisionType.REVOKED, 5),
    ]
    state = resolve_state(history)
    assert state is MappingDecisionType.REVOKED
    assert state is not MappingDecisionType.VERIFIED


def test_latest_rejection_resolves_to_rejected() -> None:
    history = [
        _decision(MappingDecisionType.VERIFIED, 0),
        _decision(MappingDecisionType.REJECTED, 5),
    ]
    assert resolve_state(history) is MappingDecisionType.REJECTED


def test_history_is_folded_in_time_order_not_input_order() -> None:
    history = [
        _decision(MappingDecisionType.STALE, 5),
        _decision(MappingDecisionType.VERIFIED, 10),
        _decision(MappingDecisionType.VERIFIED, 0),
    ]
    assert resolve_state(history) is MappingDecisionType.VERIFIED


def test_equal_timestamps_keep_input_order() -> None:
    history = [
        _decision(MappingDecisionType.VERIFIED, 0),
        _decision(MappingDecisionType.REJECTED, 0),
    ]
    assert resolve_state(history) is MappingDecisionType.REJECTED


def test_allowed_reason_codes_per_decision_type() -> None:
    assert allowed_reason_codes(MappingDecisionType.VERIFIED) == frozenset(
        reason.value for reason in VerifiedReason
    )
    assert allowed_reason_codes(MappingDecisionType.REJECTED) == frozenset(
        reason.value for reason in RejectedReason
    )
    assert allowed_reason_codes(MappingDecisionType.STALE) == frozenset(
        reason.value for reason in StaleReason
    )
    assert allowed_reason_codes(MappingDecisionType.REVOKED) == frozenset({"other"})


def test_allowed_reason_codes_match_spec_values() -> None:
    assert "confirmed_by_owner" in allowed_reason_codes(MappingDecisionType.VERIFIED)
    assert "wrong_module" in allowed_reason_codes(MappingDecisionType.REJECTED)
    assert "file_missing" in allowed_reason_codes(MappingDecisionType.STALE)
    assert "wrong_module" not in allowed_reason_codes(MappingDecisionType.VERIFIED)


@pytest.mark.parametrize("mapping_id", [0, -1])
def test_non_positive_mapping_id_is_rejected(mapping_id: int) -> None:
    with pytest.raises(ValueError):
        MappingDecisionRecord(
            mapping_id=mapping_id, decision=MappingDecisionType.VERIFIED
        )


def test_decision_must_be_enum_member() -> None:
    with pytest.raises(ValueError):
        MappingDecisionRecord(mapping_id=1, decision="verified")  # type: ignore[arg-type]


def test_record_defaults_are_owner_reported_and_nullable() -> None:
    record = MappingDecisionRecord(
        mapping_id=1, decision=MappingDecisionType.VERIFIED
    )
    assert record.actor == "owner"
    assert record.reason_code is None
    assert record.repository_commit is None
    assert record.path_hash is None
    assert record.symbol_hash is None


def test_check_stale_reports_file_missing() -> None:
    assert (
        check_stale(
            file_exists=False,
            symbol_present=False,
            stored_file_hash="a",
            current_file_hash=None,
        )
        == StaleReason.FILE_MISSING.value
    )


def test_check_stale_reports_symbol_missing() -> None:
    assert (
        check_stale(
            file_exists=True,
            symbol_present=False,
            stored_file_hash="a",
            current_file_hash="a",
        )
        == StaleReason.SYMBOL_MISSING.value
    )


def test_check_stale_reports_modified_but_valid() -> None:
    assert (
        check_stale(
            file_exists=True,
            symbol_present=True,
            stored_file_hash="a",
            current_file_hash="b",
        )
        == MODIFIED_BUT_VALID
    )


def test_check_stale_returns_none_when_unchanged() -> None:
    assert (
        check_stale(
            file_exists=True,
            symbol_present=True,
            stored_file_hash="a",
            current_file_hash="a",
        )
        is None
    )


def test_check_stale_without_hash_snapshot_is_not_stale() -> None:
    assert (
        check_stale(
            file_exists=True,
            symbol_present=True,
            stored_file_hash=None,
            current_file_hash="b",
        )
        is None
    )
