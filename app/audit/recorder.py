"""RunRecorder lifecycle과 안전 payload 기록."""

from __future__ import annotations

from dataclasses import replace

from app.audit.sanitizer import sanitize_payload
from app.domain.audit import (
    AuditEventRecord,
    AuditEventType,
    ExecutionRunRecord,
    ExecutionRunStatus,
    ExecutionRunType,
)
from app.domain.runs.context import new_run_id, utc_now


class RunRecorder:
    def __init__(self, repository) -> None:
        self.repository = repository

    def start_run(self, run_type: ExecutionRunType, **metadata) -> ExecutionRunRecord:
        now = utc_now()
        run = ExecutionRunRecord(
            run_id=new_run_id(),
            run_type=run_type,
            status=ExecutionRunStatus.RUNNING,
            started_at=now,
            **metadata,
        )
        self.repository.create_run(run)
        self.record(run.run_id, AuditEventType.RUN_CREATED, {"run_type": run_type.value})
        self.record(run.run_id, AuditEventType.RUN_STARTED, {})
        return run

    def record(
        self,
        run_id: str,
        event_type: AuditEventType,
        payload: dict,
        artifact_refs: tuple[str, ...] = (),
    ) -> AuditEventRecord:
        event = AuditEventRecord(
            run_id,
            self.repository.next_sequence(run_id),
            event_type,
            utc_now(),
            sanitize_payload(payload),
            artifact_refs,
        )
        self.repository.append_event(event)
        return event

    def complete(self, run_id: str) -> ExecutionRunRecord:
        run = self.repository.get_run(run_id)
        completed = replace(
            run, status=ExecutionRunStatus.COMPLETED, completed_at=utc_now()
        )
        self.repository.update_run(completed)
        self.record(run_id, AuditEventType.RUN_COMPLETED, {})
        return completed

    def fail(
        self, run_id: str, category: str, message: str
    ) -> ExecutionRunRecord:
        run = self.repository.get_run(run_id)
        failed = replace(
            run,
            status=ExecutionRunStatus.FAILED,
            completed_at=utc_now(),
            error_category=category,
            error_message=str(sanitize_payload(message)),
        )
        self.repository.update_run(failed)
        self.record(
            run_id,
            AuditEventType.RUN_FAILED,
            {"category": category, "message": message},
        )
        return failed
