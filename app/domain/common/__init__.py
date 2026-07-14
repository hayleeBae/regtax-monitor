"""V2 공통 계약 — enum, 버전 객체, 오류 분류, JSON 직렬화 규칙."""

from app.domain.common.enums import (
    AutomationDecision,
    ChangeType,
    RetrievalSource,
    RunStatus,
    RunType,
)
from app.domain.common.errors import DomainError, ErrorCategory
from app.domain.common.serialization import to_jsonable
from app.domain.common.version import VersionedComponent

__all__ = [
    "RunType",
    "RunStatus",
    "ChangeType",
    "AutomationDecision",
    "RetrievalSource",
    "ErrorCategory",
    "DomainError",
    "VersionedComponent",
    "to_jsonable",
]
