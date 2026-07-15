"""app.evaluation — 평가 데이터셋·지표·결과 모델 (Issue #0004).

Runner (#0005) 와 Report (#0005) 는 이 패키지에 의존한다.
이 패키지는 FastAPI / SQLAlchemy / ChromaDB / LLM SDK 를 import 하지 않는다.
"""

from app.evaluation.case import (
    CaseMetadata,
    EvaluationCase,
    ExecutionExpectation,
    ExpectedOutcome,
    ExpectedPatch,
    ExpectedReplacement,
    ExpectedRetrieval,
    LawInput,
    RepositoryFixture,
)
from app.evaluation.errors import DatasetValidationError
from app.evaluation.loader import DatasetLoader
from app.evaluation.result import (
    ArtifactReference,
    CaseResult,
    CaseStatus,
    ClassificationMetrics,
    ClassificationResult,
    EvaluationError,
    GoldenStatus,
    MRRResult,
    PatchMetrics,
    PatchResult,
    RecallAtKResult,
    RetrievalResult,
)

__all__ = [
    # case
    "EvaluationCase",
    "LawInput",
    "ExpectedOutcome",
    "ExpectedRetrieval",
    "ExpectedPatch",
    "ExpectedReplacement",
    "RepositoryFixture",
    "ExecutionExpectation",
    "CaseMetadata",
    # errors
    "DatasetValidationError",
    # loader
    "DatasetLoader",
    # result
    "CaseResult",
    "CaseStatus",
    "GoldenStatus",
    "ClassificationResult",
    "RetrievalResult",
    "PatchResult",
    "EvaluationError",
    "ArtifactReference",
    "RecallAtKResult",
    "MRRResult",
    "ClassificationMetrics",
    "PatchMetrics",
]
