"""Evaluation 전용 오류 — EVALUATION_SPEC.md §17.

DatasetValidationError 는 실행 전 검증 단계에서만 발생한다.
"""

from __future__ import annotations

from typing import Optional

from app.domain.common.errors import DomainError, ErrorCategory


class DatasetValidationError(DomainError):
    """데이터셋 유효성 검사 실패 — 실행을 시작하기 전에 발생한다."""

    def __init__(self, message: str, *, details: Optional[list[str]] = None) -> None:
        super().__init__(
            ErrorCategory.EVALUATION_ERROR,
            message,
            retryable=False,
        )
        self.details: list[str] = details or []

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["details"] = self.details
        return base
