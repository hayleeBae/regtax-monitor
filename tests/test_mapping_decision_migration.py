"""Issue #0015 mapping_decision 테이블과 legacy backfill migration 테스트."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.database import Base, _migrate
from app.db.models import Mapping, MappingDecision
from app.domain.mappings.decisions import MappingDecisionType, VerifiedReason


@pytest.fixture()
def session(monkeypatch, tmp_path):
    """임시 SQLite 파일 DB — `_migrate()`는 모듈 전역 engine을 사용한다."""
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed_mappings(session) -> None:
    session.add_all(
        [
            Mapping(id=1, article_id="a-1", path="src/tax.py", verified=True),
            Mapping(id=2, article_id="a-2", path="src/hr.py", verified=True),
            Mapping(id=3, article_id="a-3", path="src/etc.py", verified=False),
        ]
    )
    session.commit()


def _decisions(session, mapping_id: int) -> list[MappingDecision]:
    return (
        session.query(MappingDecision)
        .filter(MappingDecision.mapping_id == mapping_id)
        .order_by(MappingDecision.id)
        .all()
    )


def test_backfill_creates_legacy_event_only_for_verified_mappings(session) -> None:
    _seed_mappings(session)

    _migrate()

    session.expire_all()
    assert [d.mapping_id for d in session.query(MappingDecision).all()] == [1, 2]
    event = _decisions(session, 1)[0]
    assert event.decision == MappingDecisionType.VERIFIED.value
    assert event.reason_code == VerifiedReason.OTHER.value
    assert event.reason_text == "legacy verified backfill"
    assert event.actor == "system"
    assert isinstance(event.created_at, datetime)
    assert _decisions(session, 3) == []


def test_backfill_is_idempotent_across_repeated_migrations(session) -> None:
    _seed_mappings(session)

    _migrate()
    _migrate()
    _migrate()

    session.expire_all()
    assert session.query(MappingDecision).count() == 2


def test_backfill_skips_mapping_with_existing_decision(session) -> None:
    _seed_mappings(session)
    session.add(
        MappingDecision(
            mapping_id=1,
            decision=MappingDecisionType.REJECTED.value,
            reason_code="wrong_module",
            actor="owner",
            created_at=datetime.utcnow(),
        )
    )
    session.commit()

    _migrate()

    session.expire_all()
    assert [d.decision for d in _decisions(session, 1)] == [
        MappingDecisionType.REJECTED.value
    ]
    assert len(_decisions(session, 2)) == 1


def test_migrate_preserves_existing_mapping_rows_and_verified_column(session) -> None:
    _seed_mappings(session)

    _migrate()

    session.expire_all()
    rows = session.query(Mapping).order_by(Mapping.id).all()
    assert [(row.id, row.verified) for row in rows] == [
        (1, True),
        (2, True),
        (3, False),
    ]
