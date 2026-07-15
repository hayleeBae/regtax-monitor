"""검색 지표 — EVALUATION_SPEC.md §7.

모든 함수는 순수 함수다 (외부 I/O 없음).
"""

from __future__ import annotations

from typing import Sequence


def case_hit_at_k(
    relevant_files: Sequence[str],
    predicted_files: Sequence[str],
    k: int,
) -> bool:
    """Case Hit@K: 상위 K개에 정답 파일이 하나 이상 존재하면 True."""
    if not relevant_files:
        return True
    top_k = list(predicted_files)[:k]
    relevant_set = set(relevant_files)
    return any(f in relevant_set for f in top_k)


def file_recall_at_k(
    relevant_files: Sequence[str],
    predicted_files: Sequence[str],
    k: int,
) -> float:
    """File Recall@K = 상위 K개에서 발견된 정답 파일 수 / 전체 정답 파일 수."""
    if not relevant_files:
        return 1.0
    top_k = list(predicted_files)[:k]
    relevant_set = set(relevant_files)
    found = sum(1 for f in top_k if f in relevant_set)
    return found / len(relevant_files)


def precision_at_k(
    relevant_files: Sequence[str],
    predicted_files: Sequence[str],
    k: int,
) -> float:
    """Precision@K = 상위 K개 중 정답 파일 수 / 실제 반환 후보 수."""
    top_k = list(predicted_files)[:k]
    if not top_k:
        return 0.0
    relevant_set = set(relevant_files)
    found = sum(1 for f in top_k if f in relevant_set)
    return found / len(top_k)


def reciprocal_rank(
    relevant_files: Sequence[str],
    predicted_files: Sequence[str],
) -> float:
    """첫 번째 정답 파일의 역수 순위. 정답이 없으면 0."""
    relevant_set = set(relevant_files)
    for i, f in enumerate(predicted_files, start=1):
        if f in relevant_set:
            return 1.0 / i
    return 0.0


def mean_reciprocal_rank(
    cases: Sequence[tuple[Sequence[str], Sequence[str]]],
) -> float:
    """MRR = mean(1 / 첫 정답 rank) — EVALUATION_SPEC.md §7.

    cases: [(relevant_files, predicted_files), ...]
    케이스가 없으면 0.0.
    """
    if not cases:
        return 0.0
    total = sum(reciprocal_rank(rel, pred) for rel, pred in cases)
    return total / len(cases)


def aggregate_recall_at_k(
    cases: Sequence[tuple[Sequence[str], Sequence[str]]],
    k: int,
) -> dict[str, float]:
    """여러 케이스의 Hit@K / File Recall@K / Precision@K 평균.

    반환: {"hit_rate": float, "file_recall": float, "precision": float}
    """
    if not cases:
        return {"hit_rate": 0.0, "file_recall": 0.0, "precision": 0.0}
    hit_sum = sum(float(case_hit_at_k(rel, pred, k)) for rel, pred in cases)
    recall_sum = sum(file_recall_at_k(rel, pred, k) for rel, pred in cases)
    precision_sum = sum(precision_at_k(rel, pred, k) for rel, pred in cases)
    n = len(cases)
    return {
        "hit_rate": hit_sum / n,
        "file_recall": recall_sum / n,
        "precision": precision_sum / n,
    }
