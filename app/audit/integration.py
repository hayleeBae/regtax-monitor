"""기존 route에 audit 실패를 부분 실패로 연결하는 얇은 wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from app.audit.recorder import RunRecorder
from app.audit.repository import SqlAlchemyAuditRepository
from app.domain.audit import AuditEventType, ExecutionRunType


@dataclass
class AuditScope:
    recorder: RunRecorder | None
    run_id: str | None
    incomplete: bool = False
    error: str | None = None

    @classmethod
    def start(cls, db, run_type: ExecutionRunType, **metadata) -> "AuditScope":
        try:
            recorder = RunRecorder(SqlAlchemyAuditRepository(db))
            run = recorder.start_run(run_type, **metadata)
            return cls(recorder, run.run_id)
        except Exception as exc:
            return cls(None, None, True, f"audit start failed: {exc}")

    def record(self, event_type: AuditEventType, payload: dict) -> None:
        if self.recorder is None or self.run_id is None:
            return
        try:
            self.recorder.record(self.run_id, event_type, payload)
        except Exception as exc:
            self.incomplete = True
            self.error = f"audit record failed: {exc}"

    def complete(self) -> None:
        if self.recorder is None or self.run_id is None:
            return
        try:
            self.recorder.complete(self.run_id)
        except Exception as exc:
            self.incomplete = True
            self.error = f"audit completion failed: {exc}"

    def fail(self, category: str, message: str) -> None:
        if self.recorder is None or self.run_id is None:
            return
        try:
            self.recorder.fail(self.run_id, category, message)
        except Exception as exc:
            self.incomplete = True
            self.error = f"audit failure record failed: {exc}"

    def response_fields(self) -> dict:
        return {
            "run_id": self.run_id,
            "audit_incomplete": self.incomplete,
            **({"audit_error": self.error} if self.error else {}),
        }
