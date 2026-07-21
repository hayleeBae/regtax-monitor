"""평가 결과 집계와 결정론적 Markdown 보고서 생성."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from app.evaluation.metrics import (
    accuracy,
    aggregate_recall_at_k,
    macro_f1,
    mean_reciprocal_rank,
    per_class_metrics,
)
from app.evaluation.result import CaseResult, CaseStatus


def build_summary(results: Sequence[CaseResult]) -> dict:
    counts = Counter(result.status.value for result in results)
    count_summary = {"total": len(results)}
    for status in CaseStatus:
        count_summary[status.value] = counts[status.value]

    classified = [result.classification for result in results if result.classification]
    predictions = [item.predicted_type for item in classified]
    labels = [item.expected_type for item in classified]
    classification = {
        "accuracy": accuracy(predictions, labels),
        "macro_f1": macro_f1(predictions, labels),
        "per_type": per_class_metrics(predictions, labels),
        "n_cases": len(classified),
    }

    retrieved = [result.retrieval for result in results if result.retrieval]
    retrieval_cases = [
        (item.relevant_files, item.predicted_files) for item in retrieved
    ]
    all_k = sorted({k for item in retrieved for k in item.top_k_evaluated})
    retrieval = {
        "mrr": mean_reciprocal_rank(retrieval_cases),
        "n_cases": len(retrieved),
        "at_k": {
            str(k): aggregate_recall_at_k(retrieval_cases, k) for k in all_k
        },
    }

    patched = [result.patch for result in results if result.patch]
    matched = sum(item.expected_replacements_matched for item in patched)
    expected = sum(item.expected_replacements_total for item in patched)
    touched = sum(len(item.forbidden_files_touched) for item in patched)
    patch = {
        "replacement_rate": matched / expected if expected else 1.0,
        "matched_replacements": matched,
        "expected_replacements": expected,
        "git_apply_ok": sum(item.git_apply_ok for item in patched),
        "forbidden_files_touched": touched,
        "n_cases": len(patched),
    }

    durations = [result.duration_ms for result in results]
    latency = {
        "average_ms": sum(durations) / len(durations) if durations else 0.0,
        "maximum_ms": max(durations, default=0),
        "n_cases": len(durations),
    }
    errors = Counter(error.code for result in results for error in result.errors)
    return {
        "counts": count_summary,
        "classification": classification,
        "retrieval": retrieval,
        "patch": patch,
        "latency": latency,
        "failures_by_code": dict(sorted(errors.items())),
    }


def render_report(
    run_name: str,
    experiment_id: str,
    dataset_name: str,
    results: Sequence[CaseResult],
    summary: dict,
) -> str:
    counts = summary["counts"]
    classification = summary["classification"]
    retrieval = summary["retrieval"]
    patch = summary["patch"]
    lines = [
        "# Evaluation Report",
        "",
        "## 1. 실행 정보",
        "",
        f"- Run: `{run_name}`",
        f"- Experiment: `{experiment_id}`",
        f"- Dataset: `{dataset_name}`",
        "- 주의: `fixture_baseline`은 모델 성능이 아니라 평가 배선의 oracle 기준선입니다.",
        "",
        "## 2. 데이터셋 구성",
        "",
        f"- 전체: {counts['total']}건",
        "",
        "## 3. 전체 지표",
        "",
        f"- 통과: {counts['passed']}/{counts['total']}",
        f"- 실패/오류: {counts['failed'] + counts['error']}/{counts['total']}",
        "",
        "## 4. 유형별 분류 지표",
        "",
        f"- Accuracy: {_percent(classification['accuracy'])} "
        f"({round(classification['accuracy'] * classification['n_cases'])}/"
        f"{classification['n_cases']})",
        f"- Macro F1: {_percent(classification['macro_f1'])} "
        f"(평가 {classification['n_cases']}건)",
        "",
        "| 유형 | Precision | Recall | F1 |",
        "|---|---:|---:|---:|",
    ]
    for change_type, values in sorted(classification["per_type"].items()):
        lines.append(
            f"| {change_type} | {_percent(values['precision'])} | "
            f"{_percent(values['recall'])} | {_percent(values['f1'])} |"
        )
    lines.extend(["", "## 5. Recall@K, MRR", ""])
    for k, values in retrieval["at_k"].items():
        denominator = retrieval["n_cases"]
        numerator = round(values["hit_rate"] * denominator)
        lines.append(
            f"- Hit@{k}: {_percent(values['hit_rate'])} ({numerator}/{denominator}), "
            f"File Recall: {_percent(values['file_recall'])}, "
            f"Precision: {_percent(values['precision'])}"
        )
    lines.extend(
        [
            f"- MRR: {retrieval['mrr']:.3f} (평가 {retrieval['n_cases']}건)",
            "",
            "## 6. Provider 기여",
            "",
            "- fixture baseline에는 검색 provider가 없습니다.",
            "",
            "## 7. Patch / Golden 결과",
            "",
            f"- Replacement 일치율: {_percent(patch['replacement_rate'])} "
            f"({patch['matched_replacements']}/{patch['expected_replacements']})",
            f"- git apply 가능 표시: {patch['git_apply_ok']}/{patch['n_cases']}",
            f"- 금지 파일 접촉: {patch['forbidden_files_touched']}건",
            "- fixture baseline은 실제 patch 적용과 golden command를 실행하지 않습니다.",
            "",
            "## 8. Latency",
            "",
            f"- 평균: {summary['latency']['average_ms']:.1f}ms "
            f"(평가 {summary['latency']['n_cases']}건)",
            f"- 최대: {summary['latency']['maximum_ms']}ms",
            "",
            "## 9. 실패 분포",
            "",
        ]
    )
    if summary["failures_by_code"]:
        for code, count in summary["failures_by_code"].items():
            lines.append(f"- `{code}`: {count}건")
    else:
        lines.append("- 없음")
    lines.extend(["", "## 10. 실패 Case 목록", ""])
    failed = [result for result in results if result.status is not CaseStatus.PASSED]
    if failed:
        for result in failed:
            lines.append(f"- `{result.case_id}`: {result.status.value}")
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def render_failures(results: Sequence[CaseResult]) -> str:
    lines = ["# Evaluation Failures", ""]
    failed = [result for result in results if result.errors]
    if not failed:
        return "\n".join(lines + ["실패 없음", ""])
    for result in failed:
        lines.extend([f"## {result.case_id}", ""])
        for error in result.errors:
            lines.append(f"- `{error.code}`: {error.message}")
        lines.append("")
    return "\n".join(lines)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"

