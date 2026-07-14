"""V2 오류 분류와 도메인 예외 기반.

오류를 평문 문자열 하나로 저장하지 않기 위한 최소 계약이다
(ARCHITECTURE_V2 §11). 사용자 노출 메시지와 내부 원인을 분리하고,
재시도 가능 여부를 표시한다. 민감정보는 payload 에 담지 않는다(호출부 책임).
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """오류 분류 — 값은 ARCHITECTURE_V2 §11 목록과 정확히 일치한다."""

    INPUT_ERROR = "input_error"
    SOURCE_UNAVAILABLE = "source_unavailable"
    CLASSIFICATION_ERROR = "classification_error"
    RETRIEVAL_ERROR = "retrieval_error"
    POLICY_BLOCKED = "policy_blocked"
    LLM_ERROR = "llm_error"
    ANCHOR_ERROR = "anchor_error"
    PATCH_ERROR = "patch_error"
    GOLDEN_TEST_ERROR = "golden_test_error"
    AUDIT_ERROR = "audit_error"
    EVALUATION_ERROR = "evaluation_error"


class DomainError(Exception):
    """도메인 계층 공통 예외.

    - category: 구조화된 오류 분류
    - retryable: 재시도 가능 여부
    - message: 사용자에게 보여줄 수 있는 안전한 메시지
    - internal_detail: 내부 진단용(민감정보·원문 코드 금지). 노출 대상 아님.
    """

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        retryable: bool = False,
        internal_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.internal_detail = internal_detail

    def to_dict(self) -> dict:
        """사용자 노출용 안전 표현 — internal_detail 은 포함하지 않는다."""
        return {
            "category": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
        }
