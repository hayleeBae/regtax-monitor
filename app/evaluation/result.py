"""CaseResult 및 집계 Metric 결과 모델 — EVALUATION_SPEC.md §10.

Runner(#0005) 가 생성하고 이 모듈의 dataclass 로 표현한다.
Metric 계산 함수는 app.evaluation.metrics.* 에 분리한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CaseStatus(str, Enum):
    """케이스 실행 결과 상태 — EVALUATION_SPEC.md §10."""

    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class GoldenStatus(str, Enum):
    """골든 테스트 실행 결과 — EVALUATION_SPEC.md §8."""

    PASSED = "passed"
    FAILED = "failed"
    APPLY_FAILED = "apply_failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    ERROR = "error"


# ---------------------------------------------------------------------------
# 단계별 결과
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """분류 단계 결과."""

    predicted_type: str
    expected_type: str
    confidence: Optional[float] = None

    @property
    def correct(self) -> bool:
        return self.predicted_type == self.expected_type


@dataclass
class RetrievalResult:
    """검색 단계 결과."""

    predicted_files: list[str]
    relevant_files: list[str]
    top_k_evaluated: list[int]


@dataclass
class PatchResult:
    """Patch 생성 단계 결과."""

    patched_files: list[str]
    expected_replacements_matched: int
    expected_replacements_total: int
    forbidden_files_touched: list[str] = field(default_factory=list)
    git_apply_ok: bool = False
    golden_status: str = GoldenStatus.SKIPPED.value


@dataclass
class EvaluationError:
    """케이스 실행 중 발생한 단계별 오류."""

    code: str  # EVALUATION_SPEC.md §17 오류 코드
    message: str


@dataclass
class ArtifactReference:
    """생성된 산출물 참조."""

    name: str
    path: str
    content_hash: Optional[str] = None


@dataclass
class CaseResult:
    """케이스 실행 결과 전체 — EVALUATION_SPEC.md §10."""

    case_id: str
    status: CaseStatus
    experiment_id: str
    duration_ms: int
    classification: Optional[ClassificationResult] = None
    retrieval: Optional[RetrievalResult] = None
    patch: Optional[PatchResult] = None
    errors: list[EvaluationError] = field(default_factory=list)
    artifacts: list[ArtifactReference] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 집계 Metric 결과
# ---------------------------------------------------------------------------


@dataclass
class RecallAtKResult:
    """단일 K 값에 대한 검색 지표."""

    k: int
    hit_rate: float     # Case Hit@K
    file_recall: float  # File Recall@K
    precision: float    # Precision@K
    n_cases: int


@dataclass
class MRRResult:
    """Mean Reciprocal Rank 집계."""

    mrr: float
    n_cases: int


@dataclass
class ClassificationMetrics:
    """분류 지표 집계."""

    accuracy: float
    macro_f1: float
    per_type: dict[str, dict[str, float]]  # type → {precision, recall, f1}
    n_cases: int


@dataclass
class PatchMetrics:
    """Patch 지표 집계."""

    replacement_rate: float   # matched / expected_total
    file_coverage: float      # relevant files with patches / total relevant
    unnecessary_file_rate: float
    n_cases: int
