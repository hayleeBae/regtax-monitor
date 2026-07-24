"""SQLAlchemy audit repository — event 수정 API는 제공하지 않는다."""

from __future__ import annotations

import json
from datetime import timezone

from sqlalchemy import func

from app.db.models import AuditEvent, ExecutionRun
from app.domain.audit import (
    AuditEventRecord,
    AuditEventType,
    ExecutionRunRecord,
    ExecutionRunStatus,
    ExecutionRunType,
)


class SqlAlchemyAuditRepository:
    def __init__(self, session) -> None:
        self.session = session

    def create_run(self, run: ExecutionRunRecord) -> None:
        self.session.add(
            ExecutionRun(
                run_id=run.run_id,
                parent_run_id=run.parent_run_id,
                run_type=run.run_type.value,
                status=run.status.value,
                law_change_id=run.law_change_id,
                proposal_id=run.proposal_id,
                evaluation_run_id=run.evaluation_run_id,
                source_hash=run.source_hash,
                repository_alias=run.repository_alias,
                repository_commit=run.repository_commit,
                settings_hash=run.settings_hash,
                llm_backend=run.llm_backend,
                llm_model=run.llm_model,
                embedding_model=run.embedding_model,
                prompt_versions=json.dumps(dict(run.prompt_versions), sort_keys=True),
                started_at=_naive_utc(run.started_at),
            )
        )
        self.session.commit()

    def update_run(self, run: ExecutionRunRecord) -> None:
        row = self.session.get(ExecutionRun, run.run_id)
        if row is None:
            raise KeyError(f"run not found: {run.run_id}")
        row.status = run.status.value
        row.completed_at = _naive_utc(run.completed_at) if run.completed_at else None
        row.error_category = run.error_category
        row.error_message = run.error_message
        self.session.commit()

    def append_event(self, event: AuditEventRecord) -> None:
        self.session.add(
            AuditEvent(
                run_id=event.run_id,
                sequence_no=event.sequence_no,
                event_type=event.event_type.value,
                occurred_at=_naive_utc(event.occurred_at),
                payload=json.dumps(dict(event.payload), ensure_ascii=False, sort_keys=True),
                artifact_refs=json.dumps(list(event.artifact_refs), sort_keys=True),
            )
        )
        self.session.commit()

    def next_sequence(self, run_id: str) -> int:
        current = (
            self.session.query(func.max(AuditEvent.sequence_no))
            .filter(AuditEvent.run_id == run_id)
            .scalar()
        )
        return int(current or 0) + 1

    def get_run(self, run_id: str) -> ExecutionRunRecord:
        row = self.session.get(ExecutionRun, run_id)
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return _run_record(row)

    def list_events(self, run_id: str) -> tuple[AuditEventRecord, ...]:
        rows = (
            self.session.query(AuditEvent)
            .filter(AuditEvent.run_id == run_id)
            .order_by(AuditEvent.sequence_no)
            .all()
        )
        return tuple(
            AuditEventRecord(
                row.run_id,
                row.sequence_no,
                AuditEventType(row.event_type),
                _aware_utc(row.occurred_at),
                json.loads(row.payload or "{}"),
                tuple(json.loads(row.artifact_refs or "[]")),
            )
            for row in rows
        )

    def update_event(self, run_id: str, sequence_no: int, payload: dict) -> None:
        raise NotImplementedError("audit events are append-only")


def _run_record(row) -> ExecutionRunRecord:
    return ExecutionRunRecord(
        run_id=row.run_id,
        parent_run_id=row.parent_run_id,
        run_type=ExecutionRunType(row.run_type),
        status=ExecutionRunStatus(row.status),
        law_change_id=row.law_change_id,
        proposal_id=row.proposal_id,
        evaluation_run_id=row.evaluation_run_id,
        source_hash=row.source_hash,
        repository_alias=row.repository_alias,
        repository_commit=row.repository_commit,
        settings_hash=row.settings_hash,
        llm_backend=row.llm_backend,
        llm_model=row.llm_model,
        embedding_model=row.embedding_model,
        prompt_versions=json.loads(row.prompt_versions or "{}"),
        started_at=_aware_utc(row.started_at),
        completed_at=_aware_utc(row.completed_at) if row.completed_at else None,
        error_category=row.error_category,
        error_message=row.error_message,
    )


def _naive_utc(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _aware_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
