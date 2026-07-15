"""EvaluationCase 스키마 — EVALUATION_SPEC.md §4.

평가 케이스 한 건을 표현하는 불변 값 객체 계층.
이 모듈은 표준 라이브러리에만 의존한다 (FastAPI/SQLAlchemy/LLM SDK 금지).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Law input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LawInput:
    """평가에 사용되는 법령 변경 입력."""

    law_name: str
    tier: str  # law | enforcement_decree | enforcement_rule | admin_rule
    before_text: str
    after_text: str
    article: Optional[str] = None
    effective_date: Optional[str] = None  # ISO 8601 date string (YYYY-MM-DD)


# ---------------------------------------------------------------------------
# Expected retrieval / patch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedReplacement:
    """예상 patch replacement — path 내 before → after 교체."""

    path: str
    before: str
    after: str
    match_mode: str = "exact"  # exact | normalized_text


@dataclass(frozen=True)
class ExpectedRetrieval:
    """검색 단계의 기대값."""

    relevant_files: tuple[str, ...]
    primary_files: tuple[str, ...] = ()
    relevant_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExpectedPatch:
    """Patch 단계의 기대값."""

    expected_replacements: tuple[ExpectedReplacement, ...]
    forbidden_files: tuple[str, ...] = ()
    require_git_apply: bool = False
    require_golden_pass: bool = False


@dataclass(frozen=True)
class ExpectedOutcome:
    """케이스의 종합 기대 결과."""

    change_type: str  # ChangeType 값 문자열
    automation_decision: Optional[str] = None  # AutomationDecision 값 문자열
    retrieval: Optional[ExpectedRetrieval] = None
    patch: Optional[ExpectedPatch] = None


# ---------------------------------------------------------------------------
# Repository fixture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryFixture:
    """평가 대상 코드베이스 위치."""

    fixture_type: str  # directory | git_commit
    path: str
    base_commit: Optional[str] = None
    answer_commit: Optional[str] = None
    golden_command: Optional[str] = None


# ---------------------------------------------------------------------------
# Execution expectation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionExpectation:
    """실행 범위와 파라미터."""

    evaluate_classification: bool = True
    evaluate_retrieval: bool = True
    evaluate_patch: bool = False
    top_k: tuple[int, ...] = (1, 3, 5, 10)
    timeout_seconds: int = 600


# ---------------------------------------------------------------------------
# Case metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseMetadata:
    """케이스 출처와 품질 정보."""

    source: str  # synthetic | historical | real
    reviewed: bool = False
    schema_version: str = "1"


# ---------------------------------------------------------------------------
# Top-level EvaluationCase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationCase:
    """평가 케이스 한 건의 완전한 표현 — EVALUATION_SPEC.md §4."""

    case_id: str
    title: str
    domain: str
    tags: tuple[str, ...]
    law: LawInput
    expected: ExpectedOutcome
    repository: RepositoryFixture
    execution: ExecutionExpectation
    metadata: CaseMetadata
