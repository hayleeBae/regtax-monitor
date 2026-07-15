"""분류 지표 — EVALUATION_SPEC.md §6.

모든 함수는 순수 함수다 (외부 I/O 없음).
"""

from __future__ import annotations

from typing import Sequence


def accuracy(
    predictions: Sequence[str],
    labels: Sequence[str],
) -> float:
    """Accuracy = 맞은 수 / 전체 케이스 수."""
    if not labels:
        return 0.0
    n_correct = sum(1 for p, label in zip(predictions, labels) if p == label)
    return n_correct / len(labels)


def _per_class_raw(
    predictions: Sequence[str],
    labels: Sequence[str],
) -> dict[str, dict[str, int]]:
    """클래스별 TP / FP / FN 계산."""
    classes: set[str] = set(labels) | set(predictions)
    stats: dict[str, dict[str, int]] = {
        c: {"tp": 0, "fp": 0, "fn": 0} for c in classes
    }
    for pred, label in zip(predictions, labels):
        if pred == label:
            stats[label]["tp"] += 1
        else:
            stats[pred]["fp"] += 1
            stats[label]["fn"] += 1
    return stats


def per_class_metrics(
    predictions: Sequence[str],
    labels: Sequence[str],
) -> dict[str, dict[str, float]]:
    """유형별 Precision / Recall / F1.

    반환: {change_type: {"precision": float, "recall": float, "f1": float}}
    """
    stats = _per_class_raw(predictions, labels)
    result: dict[str, dict[str, float]] = {}
    for cls, s in stats.items():
        tp, fp, fn = s["tp"], s["fp"], s["fn"]
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        result[cls] = {"precision": prec, "recall": rec, "f1": f1}
    return result


def macro_f1(
    predictions: Sequence[str],
    labels: Sequence[str],
) -> float:
    """Macro F1 = 정답 클래스별 F1 단순 평균 — EVALUATION_SPEC.md §6."""
    if not labels:
        return 0.0
    label_classes = set(labels)
    per_cls = per_class_metrics(predictions, labels)
    f1s = [per_cls[c]["f1"] for c in label_classes if c in per_cls]
    return sum(f1s) / len(f1s) if f1s else 0.0


def low_confidence_rate(
    confidences: Sequence[float],
    threshold: float = 0.50,
) -> float:
    """confidence < threshold 인 케이스 비율."""
    if not confidences:
        return 0.0
    low = sum(1 for c in confidences if c < threshold)
    return low / len(confidences)


def high_confidence_wrong_rate(
    predictions: Sequence[str],
    labels: Sequence[str],
    confidences: Sequence[float],
    threshold: float = 0.80,
) -> float:
    """confidence >= threshold 이면서 틀린 케이스 비율 — EVALUATION_SPEC.md §6."""
    if not labels:
        return 0.0
    wrong_high = sum(
        1
        for pred, label, conf in zip(predictions, labels, confidences)
        if conf >= threshold and pred != label
    )
    return wrong_high / len(labels)
