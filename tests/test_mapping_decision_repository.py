"""Issue #0015 append-only 매핑 결정 repository 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Mapping
from app.domain.mappings.decisions import (
    MappingDecisionRecord,
    MappingDecisionType,
    RejectedReason,
    VerifiedReason,
)
from app.mappings.repository import SqlAlchemyMappingDecisionRepository

_BASE_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'decisions.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all(
        [
            Mapping(id=1, article_id="a-1", path="src/tax.py", verified=False),
            Mapping(id=2, article_id="a-2", path="src/hr.py", verified=False),
        ]
    )
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def repo(session):
    return SqlAlchemyMappingDecisionRepository(session)


def _record(
    mapping_id: int,
    decision: MappingDecisionType,
    *,
    minutes: int,
    reason_code: str | None = None,
) -> MappingDecisionRecord:
    return MappingDecisionRecord(
        mapping_id=mapping_id,
        decision=decision,
        reason_code=reason_code,
        created_at=_BASE_TIME + timedelta(minutes=minutes),
    )


def test_append_returns_id_and_lists_history_in_time_order(repo) -> None:
    second = repo.append(
        _record(
            1,
            MappingDecisionType.STALE,
            minutes=10,
            reason_code="content_changed",
        )
    )
    first = repo.append(
        MappingDecisionRecord(
            mapping_id=1,
            decision=MappingDecisionType.VERIFIED,
            reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
            reason_text="담당자 확인",
            repository_commit="abc123",
            path_hash="p-hash",
            symbol_hash="s-hash",
            actor="haylee",
            created_at=_BASE_TIME,
        )
    )

    assert first > 0 and second > 0 and first != second

    history = repo.list_for_mapping(1)
    assert [r.decision for r in history] == [
        MappingDecisionType.VERIFIED,
        MappingDecisionType.STALE,
    ]
    verified = history[0]
    assert verified.reason_code == VerifiedReason.CONFIRMED_BY_OWNER.value
    assert verified.reason_text == "담당자 확인"
    assert verified.repository_commit == "abc123"
    assert verified.path_hash == "p-hash"
    assert verified.symbol_hash == "s-hash"
    assert verified.actor == "haylee"
    assert verified.created_at == _BASE_TIME


def test_append_fills_created_at_when_missing(repo) -> None:
    before = datetime.now(timezone.utc)

    repo.append(
        MappingDecisionRecord(
            mapping_id=1,
            decision=MappingDecisionType.VERIFIED,
            reason_code=VerifiedReason.OTHER.value,
        )
    )

    (stored,) = repo.list_for_mapping(1)
    assert stored.created_at.tzinfo is timezone.utc
    assert stored.created_at >= before - timedelta(seconds=5)


def test_current_state_follows_verified_then_revoked(repo) -> None:
    assert repo.current_state(1) is None

    repo.append(
        _record(
            1,
            MappingDecisionType.VERIFIED,
            minutes=0,
            reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
        )
    )
    assert repo.current_state(1) is MappingDecisionType.VERIFIED

    repo.append(_record(1, MappingDecisionType.REVOKED, minutes=5))
    assert repo.current_state(1) is MappingDecisionType.REVOKED


def test_current_state_returns_verified_after_stale_then_reverified(repo) -> None:
    repo.append(
        _record(
            1,
            MappingDecisionType.VERIFIED,
            minutes=0,
            reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
        )
    )
    repo.append(
        _record(1, MappingDecisionType.STALE, minutes=1, reason_code="file_missing")
    )
    repo.append(
        _record(
            1,
            MappingDecisionType.VERIFIED,
            minutes=2,
            reason_code=VerifiedReason.GOLDEN_TEST_CONFIRMED.value,
        )
    )

    assert repo.current_state(1) is MappingDecisionType.VERIFIED


def test_histories_of_different_mappings_do_not_mix(repo) -> None:
    repo.append(
        _record(
            1,
            MappingDecisionType.VERIFIED,
            minutes=0,
            reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
        )
    )
    repo.append(
        _record(
            2,
            MappingDecisionType.REJECTED,
            minutes=1,
            reason_code=RejectedReason.WRONG_MODULE.value,
        )
    )

    assert [r.mapping_id for r in repo.list_for_mapping(1)] == [1]
    assert [r.mapping_id for r in repo.list_for_mapping(2)] == [2]
    assert repo.current_state(1) is MappingDecisionType.VERIFIED
    assert repo.current_state(2) is MappingDecisionType.REJECTED
    assert repo.list_for_mapping(3) == ()
    assert repo.current_state(3) is None


def test_update_is_not_supported(repo) -> None:
    with pytest.raises(NotImplementedError):
        repo.update(1, decision=MappingDecisionType.VERIFIED)
