"""SQLAlchemy 매핑 결정 repository — 결정 수정 API는 제공하지 않는다."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import MappingDecision
from app.domain.mappings.decisions import (
    MappingDecisionRecord,
    MappingDecisionType,
    resolve_state,
)


class SqlAlchemyMappingDecisionRepository:
    def __init__(self, session) -> None:
        self.session = session

    def append(self, record: MappingDecisionRecord) -> int:
        created_at = record.created_at or datetime.utcnow()
        row = MappingDecision(
            mapping_id=record.mapping_id,
            decision=record.decision.value,
            reason_code=record.reason_code,
            reason_text=record.reason_text,
            repository_commit=record.repository_commit,
            path_hash=record.path_hash,
            symbol_hash=record.symbol_hash,
            actor=record.actor,
            created_at=_naive_utc(created_at),
        )
        self.session.add(row)
        self.session.commit()
        return int(row.id)

    def list_for_mapping(self, mapping_id: int) -> tuple[MappingDecisionRecord, ...]:
        rows = (
            self.session.query(MappingDecision)
            .filter(MappingDecision.mapping_id == mapping_id)
            .order_by(MappingDecision.created_at, MappingDecision.id)
            .all()
        )
        return tuple(_decision_record(row) for row in rows)

    def current_state(self, mapping_id: int) -> MappingDecisionType | None:
        return resolve_state(self.list_for_mapping(mapping_id))

    def update(self, *args, **kwargs) -> None:
        raise NotImplementedError("mapping decisions are append-only")


def _decision_record(row) -> MappingDecisionRecord:
    return MappingDecisionRecord(
        mapping_id=row.mapping_id,
        decision=MappingDecisionType(row.decision),
        reason_code=row.reason_code,
        reason_text=row.reason_text,
        repository_commit=row.repository_commit,
        path_hash=row.path_hash,
        symbol_hash=row.symbol_hash,
        actor=row.actor,
        created_at=_aware_utc(row.created_at),
    )


def _naive_utc(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _aware_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
