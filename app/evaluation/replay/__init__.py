"""과거 개정 replay fixture 의 순수 계약 (Issue #0017, ADR-010).

선언만 담는다 — git 실행·worktree·파일 쓰기는 #0018 runner 의 책임이다.
"""

from app.evaluation.replay.fixture import (
    REPLAY_SCHEMA_VERSION,
    ArtifactKind,
    PrivacyMode,
    ReplayExecution,
    ReplayFixture,
    ReplayRepository,
    ReplayScope,
    allowed_artifacts,
)

__all__ = [
    "REPLAY_SCHEMA_VERSION",
    "ArtifactKind",
    "PrivacyMode",
    "ReplayExecution",
    "ReplayFixture",
    "ReplayRepository",
    "ReplayScope",
    "allowed_artifacts",
]
