"""Issue #0005 평가 실행기·보고서 계약 테스트."""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.case import EvaluationCase
from app.evaluation.experiments import EvaluationContext, FixtureBaselineExperiment
from app.evaluation.result import CaseResult, CaseStatus
from app.evaluation.runner import EvaluationRunner, RunConfiguration, main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "core.yaml"
REQUIRED_RESULT_FILES = {
    "manifest.json",
    "config_snapshot.json",
    "summary.json",
    "cases.jsonl",
    "report.md",
    "failures.md",
}


class _FailingExperiment:
    experiment_id = "failing_fixture"

    def prepare(self, context: EvaluationContext) -> None:
        return None

    def run_case(
        self,
        case: EvaluationCase,
        context: EvaluationContext,
    ) -> CaseResult:
        if case.case_id == "tax_dependent_deduction_value_002":
            raise RuntimeError("deliberate case failure")
        return CaseResult(
            case_id=case.case_id,
            status=CaseStatus.PASSED,
            experiment_id=self.experiment_id,
            duration_ms=0,
        )

    def close(self) -> None:
        return None


def _configuration(tmp_path: Path, run_name: str = "test-run") -> RunConfiguration:
    return RunConfiguration(
        dataset_path=CORE_DATASET,
        result_root=tmp_path,
        run_name=run_name,
        case_ids=(
            "tax_child_credit_value_001",
            "tax_dependent_deduction_value_002",
            "tax_earned_income_credit_value_003",
        ),
    )


def test_fixture_baseline_runs_three_cases_and_writes_all_outputs(tmp_path: Path) -> None:
    run = EvaluationRunner(PROJECT_ROOT).run(
        _configuration(tmp_path),
        FixtureBaselineExperiment(),
    )

    assert run.summary["counts"] == {
        "total": 3,
        "passed": 3,
        "partial": 0,
        "failed": 0,
        "skipped": 0,
        "error": 0,
    }
    assert REQUIRED_RESULT_FILES <= {path.name for path in run.output_dir.iterdir()}
    assert (run.output_dir / "artifacts").is_dir()
    assert run.summary["classification"]["accuracy"] == 1.0
    assert run.summary["retrieval"]["mrr"] == 1.0
    assert run.summary["patch"]["replacement_rate"] == 1.0


def test_case_error_is_isolated_and_recorded(tmp_path: Path) -> None:
    run = EvaluationRunner(PROJECT_ROOT).run(
        _configuration(tmp_path),
        _FailingExperiment(),
    )

    assert run.summary["counts"]["passed"] == 2
    assert run.summary["counts"]["error"] == 1
    failed = [result for result in run.results if result.status is CaseStatus.ERROR]
    assert failed[0].case_id == "tax_dependent_deduction_value_002"
    assert failed[0].errors[0].code == "internal_error"
    failures = (run.output_dir / "failures.md").read_text(encoding="utf-8")
    assert "deliberate case failure" in failures


def test_manifest_hash_matches_deterministic_result_payload(tmp_path: Path) -> None:
    run = EvaluationRunner(PROJECT_ROOT).run(
        _configuration(tmp_path),
        FixtureBaselineExperiment(),
    )
    manifest = json.loads((run.output_dir / "manifest.json").read_text())

    assert len(manifest["dataset_hash"]) == 64
    assert len(manifest["result_hash"]) == 64
    assert manifest["experiment_id"] == "fixture_baseline"
    assert manifest["case_count"] == 3


def test_same_input_produces_same_summary_cases_and_report(tmp_path: Path) -> None:
    runner = EvaluationRunner(PROJECT_ROOT)
    first = runner.run(
        _configuration(tmp_path / "first", run_name="stable"),
        FixtureBaselineExperiment(),
    )
    second = runner.run(
        _configuration(tmp_path / "second", run_name="stable"),
        FixtureBaselineExperiment(),
    )

    for filename in ("summary.json", "cases.jsonl", "report.md", "failures.md"):
        assert (first.output_dir / filename).read_bytes() == (
            second.output_dir / filename
        ).read_bytes()


def test_tag_filter_and_dry_run_do_not_execute_cases(tmp_path: Path) -> None:
    config = RunConfiguration(
        dataset_path=CORE_DATASET,
        result_root=tmp_path,
        run_name="dry-run",
        tags=("rate_change",),
        dry_run=True,
    )
    run = EvaluationRunner(PROJECT_ROOT).run(config, _FailingExperiment())

    assert run.summary["counts"]["total"] == 3
    assert run.summary["counts"]["skipped"] == 3
    assert all(result.status is CaseStatus.SKIPPED for result in run.results)


def test_cli_fixture_baseline_smoke(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--dataset",
            str(CORE_DATASET),
            "--experiment",
            "fixture_baseline",
            "--result-dir",
            str(tmp_path),
            "--run-name",
            "cli-smoke",
            "--case-id",
            "tax_child_credit_value_001",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "cli-smoke" / "summary.json").is_file()
