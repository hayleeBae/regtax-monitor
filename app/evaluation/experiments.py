"""평가 실험 계약과 네트워크 없는 fixture 기준선 실험."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.evaluation.case import EvaluationCase
from app.evaluation.result import (
    CaseResult,
    CaseStatus,
    ClassificationResult,
    GoldenStatus,
    PatchResult,
    RetrievalResult,
)


@dataclass(frozen=True)
class EvaluationContext:
    """실험에 허용된 격리 경로와 실행 설정."""

    project_root: Path
    output_dir: Path
    timeout_seconds: int | None = None


class EvaluationExperiment(Protocol):
    """서로 다른 평가 전략이 구현해야 하는 최소 계약."""

    experiment_id: str

    def prepare(self, context: EvaluationContext) -> None: ...

    def run_case(
        self,
        case: EvaluationCase,
        context: EvaluationContext,
    ) -> CaseResult: ...

    def close(self) -> None: ...


class FixtureBaselineExperiment:
    """평가 배선 검증용 결정론적 oracle 기준선.

    모델을 호출하지 않는다. 정답 분류·검색을 그대로 반환하고, patch는 fixture의
    원본 문자열 존재 여부만 검사한다. 따라서 이 결과는 모델 품질 점수가 아니다.
    """

    experiment_id = "fixture_baseline"

    def prepare(self, context: EvaluationContext) -> None:
        return None

    def run_case(
        self,
        case: EvaluationCase,
        context: EvaluationContext,
    ) -> CaseResult:
        classification = None
        if case.execution.evaluate_classification:
            classification = ClassificationResult(
                predicted_type=case.expected.change_type,
                expected_type=case.expected.change_type,
                confidence=1.0,
            )

        retrieval = None
        if case.execution.evaluate_retrieval and case.expected.retrieval is not None:
            relevant = list(case.expected.retrieval.relevant_files)
            retrieval = RetrievalResult(
                predicted_files=relevant,
                relevant_files=relevant,
                top_k_evaluated=list(case.execution.top_k),
            )

        patch = None
        status = CaseStatus.PASSED
        if case.execution.evaluate_patch and case.expected.patch is not None:
            fixture_root = context.project_root / case.repository.path
            matched = 0
            patched_files: list[str] = []
            for replacement in case.expected.patch.expected_replacements:
                source = (fixture_root / replacement.path).read_text(encoding="utf-8")
                before = replacement.before
                if replacement.match_mode == "normalized_text":
                    found = " ".join(before.split()) in " ".join(source.split())
                else:
                    found = before in source
                if found:
                    matched += 1
                    if replacement.path not in patched_files:
                        patched_files.append(replacement.path)
            total = len(case.expected.patch.expected_replacements)
            status = CaseStatus.PASSED if matched == total else CaseStatus.FAILED
            patch = PatchResult(
                patched_files=patched_files,
                expected_replacements_matched=matched,
                expected_replacements_total=total,
                # 이 실험은 working tree를 만들거나 git apply를 실행하지 않는다.
                git_apply_ok=False,
                golden_status=GoldenStatus.SKIPPED.value,
            )

        return CaseResult(
            case_id=case.case_id,
            status=status,
            experiment_id=self.experiment_id,
            duration_ms=0,
            classification=classification,
            retrieval=retrieval,
            patch=patch,
        )

    def close(self) -> None:
        return None
