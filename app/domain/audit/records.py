"""#0013 실행과 append-only event 값 객체."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ExecutionRunType(str, Enum):
    COLLECT = "collect"
    ANALYZE = "analyze"
    CLASSIFY = "classify"
    MAP = "map"
    APPLY = "apply"
    GOLDEN = "golden"
    APPROVE = "approve"
    REJECT = "reject"
    EVALUATION = "evaluation"
    HISTORICAL_REPLAY = "historical_replay"


class ExecutionRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditEventType(str, Enum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    NORMALIZATION_COMPLETED = "normalization_completed"
    ANALYSIS_REQUESTED = "analysis_requested"
    ANALYSIS_COMPLETED = "analysis_completed"
    CLASSIFICATION_COMPLETED = "classification_completed"
    RETRIEVAL_PROVIDER_COMPLETED = "retrieval_provider_completed"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    POLICY_DECIDED = "policy_decided"
    EDIT_REQUESTED = "edit_requested"
    EDIT_COMPLETED = "edit_completed"
    ANCHOR_VALIDATION_FAILED = "anchor_validation_failed"
    RETRY_REQUESTED = "retry_requested"
    RETRY_COMPLETED = "retry_completed"
    PATCH_BUILT = "patch_built"
    PATCH_VALIDATION_COMPLETED = "patch_validation_completed"
    GOLDEN_STARTED = "golden_started"
    GOLDEN_COMPLETED = "golden_completed"
    PROPOSAL_CREATED = "proposal_created"
    PROPOSAL_APPROVED = "proposal_approved"
    PROPOSAL_REJECTED = "proposal_rejected"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


@dataclass(frozen=True)
class ExecutionRunRecord:
    run_id: str
    run_type: ExecutionRunType
    status: ExecutionRunStatus
    started_at: datetime
    parent_run_id: str | None = None
    law_change_id: int | None = None
    proposal_id: int | None = None
    evaluation_run_id: str | None = None
    source_hash: str | None = None
    repository_alias: str | None = None
    repository_commit: str | None = None
    settings_hash: str | None = None
    llm_backend: str | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
    prompt_versions: Mapping[str, str] = field(default_factory=dict)
    completed_at: datetime | None = None
    error_category: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        object.__setattr__(
            self, "prompt_versions", MappingProxyType(dict(self.prompt_versions))
        )


@dataclass(frozen=True)
class AuditEventRecord:
    run_id: str
    sequence_no: int
    event_type: AuditEventType
    occurred_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.sequence_no < 1:
            raise ValueError("sequence_no must be positive")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))

