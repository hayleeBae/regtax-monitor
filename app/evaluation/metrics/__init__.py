"""app.evaluation.metrics — 분류·검색·patch 지표 함수 공개 API."""

from app.evaluation.metrics.classification import (
    accuracy,
    high_confidence_wrong_rate,
    low_confidence_rate,
    macro_f1,
    per_class_metrics,
)
from app.evaluation.metrics.patch import (
    file_coverage,
    forbidden_file_touched,
    patch_replacement_rate,
    unnecessary_file_rate,
)
from app.evaluation.metrics.retrieval import (
    aggregate_recall_at_k,
    case_hit_at_k,
    file_recall_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    reciprocal_rank,
)

__all__ = [
    # retrieval
    "case_hit_at_k",
    "file_recall_at_k",
    "precision_at_k",
    "reciprocal_rank",
    "mean_reciprocal_rank",
    "aggregate_recall_at_k",
    # classification
    "accuracy",
    "per_class_metrics",
    "macro_f1",
    "low_confidence_rate",
    "high_confidence_wrong_rate",
    # patch
    "patch_replacement_rate",
    "file_coverage",
    "unnecessary_file_rate",
    "forbidden_file_touched",
]
