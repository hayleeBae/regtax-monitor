"""감사 artifact의 외부 노출 안전 참조."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    artifact_type: str
    relative_path: str
    sha256: str
    size: int
    media_type: str
    created_at: datetime
    contains_code: bool = False
    redacted: bool = False

