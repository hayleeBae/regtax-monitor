"""Issue #0013 실행 run과 append-only audit event 테스트."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.audit.recorder import RunRecorder
from app.audit.repository import SqlAlchemyAuditRepository
from app.audit.sanitizer import sanitize_payload, stable_settings_hash
from app.db.database import Base
from app.domain.audit import AuditEventType, ExecutionRunStatus, ExecutionRunType


def _repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return SqlAlchemyAuditRepository(session), session


def test_run_lifecycle_and_event_sequence_are_persisted() -> None:
    repository, _session = _repository()
    recorder = RunRecorder(repository)
    run = recorder.start_run(
        ExecutionRunType.ANALYZE,
        law_change_id=7,
        source_hash="sha256:source",
        settings_hash="sha256:settings",
    )
    recorder.record(run.run_id, AuditEventType.NORMALIZATION_COMPLETED, {"count": 2})
    completed = recorder.complete(run.run_id)

    stored = repository.get_run(run.run_id)
    events = repository.list_events(run.run_id)
    assert stored.status is ExecutionRunStatus.COMPLETED
    assert completed.status is ExecutionRunStatus.COMPLETED
    assert [event.sequence_no for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        AuditEventType.RUN_CREATED,
        AuditEventType.RUN_STARTED,
        AuditEventType.NORMALIZATION_COMPLETED,
        AuditEventType.RUN_COMPLETED,
    ]


def test_events_are_append_only() -> None:
    repository, _session = _repository()
    recorder = RunRecorder(repository)
    run = recorder.start_run(ExecutionRunType.MAP)
    recorder.record(run.run_id, AuditEventType.RETRIEVAL_COMPLETED, {"count": 1})

    try:
        repository.update_event(run.run_id, 3, {"count": 2})
    except NotImplementedError as exc:
        assert "append-only" in str(exc)
    else:
        raise AssertionError("audit event update must be rejected")


def test_secret_keys_and_secret_like_strings_are_redacted() -> None:
    payload = sanitize_payload(
        {
            "model": "qwen",
            "api_key": "should-not-survive",
            "nested": {"authorization": "Bearer private-token"},
            "message": "token=abcdefghijk password=very-secret",
        }
    )
    rendered = str(payload)
    assert payload["model"] == "qwen"
    assert "should-not-survive" not in rendered
    assert "private-token" not in rendered
    assert "abcdefghijk" not in rendered
    assert "very-secret" not in rendered


def test_settings_hash_is_stable_and_excludes_secret_path_and_time() -> None:
    first = stable_settings_hash(
        {
            "model": "qwen",
            "top_k": 5,
            "api_key": "one",
            "repo_root": "/private/path/a",
            "timestamp": "now",
        }
    )
    second = stable_settings_hash(
        {
            "timestamp": "later",
            "repo_root": "/different/path",
            "api_key": "two",
            "top_k": 5,
            "model": "qwen",
        }
    )
    assert first == second
    assert first.startswith("sha256:")


def test_recorder_failure_is_visible_to_caller() -> None:
    class BrokenRepository:
        def create_run(self, run):
            raise RuntimeError("audit database unavailable")

    recorder = RunRecorder(BrokenRepository())
    try:
        recorder.start_run(ExecutionRunType.APPLY)
    except RuntimeError as exc:
        assert "audit database unavailable" in str(exc)
    else:
        raise AssertionError("audit failure must not be swallowed")


def test_failed_run_records_safe_error_category() -> None:
    repository, _session = _repository()
    recorder = RunRecorder(repository)
    run = recorder.start_run(ExecutionRunType.APPLY)
    failed = recorder.fail(run.run_id, "policy_blocked", "초안 생성이 차단됨")

    assert failed.status is ExecutionRunStatus.FAILED
    assert repository.list_events(run.run_id)[-1].event_type is AuditEventType.RUN_FAILED
