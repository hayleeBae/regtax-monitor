"""평가 case runner, 결과 저장 및 CLI 진입점."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from app.domain.common.serialization import to_jsonable
from app.evaluation.case import EvaluationCase
from app.evaluation.experiments import (
    EvaluationContext,
    EvaluationExperiment,
    FixtureBaselineExperiment,
)
from app.evaluation.loader import DatasetLoader
from app.evaluation.report import build_summary, render_failures, render_report
from app.evaluation.result import CaseResult, CaseStatus, EvaluationError


@dataclass(frozen=True)
class RunConfiguration:
    dataset_path: Path
    result_root: Path
    run_name: str | None = None
    case_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    max_workers: int = 1
    fail_fast: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class EvaluationRun:
    output_dir: Path
    results: tuple[CaseResult, ...]
    summary: dict


class EvaluationRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def run(
        self,
        configuration: RunConfiguration,
        experiment: EvaluationExperiment,
    ) -> EvaluationRun:
        dataset_path = configuration.dataset_path.resolve()
        cases = DatasetLoader(self.project_root, check_paths=True).load_yaml(dataset_path)
        cases = self._filter_cases(cases, configuration)
        run_name = configuration.run_name or self._default_run_name(dataset_path)
        output_dir = configuration.result_root.resolve() / run_name
        output_dir.mkdir(parents=True, exist_ok=False)
        (output_dir / "artifacts").mkdir()
        context = EvaluationContext(
            project_root=self.project_root,
            output_dir=output_dir,
            timeout_seconds=configuration.timeout_seconds,
        )

        results: list[CaseResult] = []
        if configuration.dry_run:
            results = [
                CaseResult(
                    case_id=case.case_id,
                    status=CaseStatus.SKIPPED,
                    experiment_id=experiment.experiment_id,
                    duration_ms=0,
                )
                for case in cases
            ]
        else:
            try:
                experiment.prepare(context)
            except Exception as exc:
                results = [
                    self._error_result(case, experiment.experiment_id, "prepare_failed", exc)
                    for case in cases
                ]
            else:
                for case in cases:
                    started = time.monotonic()
                    try:
                        result = experiment.run_case(case, context)
                    except Exception as exc:
                        elapsed = int((time.monotonic() - started) * 1000)
                        result = self._error_result(
                            case,
                            experiment.experiment_id,
                            "internal_error",
                            exc,
                            elapsed,
                        )
                    results.append(result)
                    if configuration.fail_fast and result.status in {
                        CaseStatus.FAILED,
                        CaseStatus.ERROR,
                    }:
                        break
            finally:
                experiment.close()

        summary = build_summary(results)
        self._write_outputs(
            configuration,
            experiment.experiment_id,
            dataset_path,
            run_name,
            output_dir,
            results,
            summary,
        )
        return EvaluationRun(output_dir, tuple(results), summary)

    @staticmethod
    def _filter_cases(
        cases: Sequence[EvaluationCase],
        configuration: RunConfiguration,
    ) -> list[EvaluationCase]:
        selected = list(cases)
        if configuration.case_ids:
            wanted = set(configuration.case_ids)
            selected = [case for case in selected if case.case_id in wanted]
        if configuration.tags:
            wanted_tags = set(configuration.tags)
            selected = [case for case in selected if wanted_tags & set(case.tags)]
        return selected

    @staticmethod
    def _error_result(
        case: EvaluationCase,
        experiment_id: str,
        code: str,
        exc: Exception,
        duration_ms: int = 0,
    ) -> CaseResult:
        return CaseResult(
            case_id=case.case_id,
            status=CaseStatus.ERROR,
            experiment_id=experiment_id,
            duration_ms=duration_ms,
            errors=[EvaluationError(code=code, message=str(exc))],
        )

    @staticmethod
    def _default_run_name(dataset_path: Path) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{dataset_path.stem}-{timestamp}"

    def _write_outputs(
        self,
        configuration: RunConfiguration,
        experiment_id: str,
        dataset_path: Path,
        run_name: str,
        output_dir: Path,
        results: Sequence[CaseResult],
        summary: dict,
    ) -> None:
        cases_text = "".join(
            _json_dumps(to_jsonable(result), compact=True) + "\n" for result in results
        )
        summary_text = _json_dumps(summary) + "\n"
        report_text = render_report(
            run_name, experiment_id, dataset_path.name, results, summary
        )
        failures_text = render_failures(results)
        result_hash = _sha256_text(summary_text + cases_text + report_text + failures_text)
        config_snapshot = {
            "dataset": str(configuration.dataset_path),
            "experiment_id": experiment_id,
            "run_name": run_name,
            "case_ids": list(configuration.case_ids),
            "tags": list(configuration.tags),
            "timeout_seconds": configuration.timeout_seconds,
            "max_workers": configuration.max_workers,
            "fail_fast": configuration.fail_fast,
            "dry_run": configuration.dry_run,
            "store_code_snippets": False,
        }
        manifest = {
            "run_name": run_name,
            "experiment_id": experiment_id,
            "dataset_hash": _sha256_bytes(dataset_path.read_bytes()),
            "result_hash": result_hash,
            "case_count": len(results),
            "git_commit": self._git_commit(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "model": None,
            "prompt_version": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (output_dir / "config_snapshot.json").write_text(
            _json_dumps(config_snapshot) + "\n", encoding="utf-8"
        )
        (output_dir / "summary.json").write_text(summary_text, encoding="utf-8")
        (output_dir / "cases.jsonl").write_text(cases_text, encoding="utf-8")
        (output_dir / "report.md").write_text(report_text, encoding="utf-8")
        (output_dir / "failures.md").write_text(failures_text, encoding="utf-8")
        (output_dir / "manifest.json").write_text(
            _json_dumps(manifest) + "\n", encoding="utf-8"
        )

    def _git_commit(self) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() or None


def _json_dumps(value: object, compact: bool = False) -> str:
    separators = (",", ":") if compact else None
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=separators,
        sort_keys=True,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run regtax evaluation fixtures")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--experiment", default="fixture_baseline")
    parser.add_argument("--result-dir", "--output", dest="result_root", type=Path, required=True)
    parser.add_argument("--run-name")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--fail-fast", choices=("true", "false"), default="false")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.experiment != "fixture_baseline":
        _parser().error(f"unsupported experiment: {args.experiment}")
    if args.config is not None and not args.config.is_file():
        _parser().error(f"config file not found: {args.config}")
    project_root = Path(__file__).resolve().parents[2]
    configuration = RunConfiguration(
        dataset_path=args.dataset,
        result_root=args.result_root,
        run_name=args.run_name,
        case_ids=tuple(args.case_id),
        tags=tuple(args.tag),
        timeout_seconds=args.timeout,
        max_workers=args.max_workers,
        fail_fast=args.fail_fast == "true",
        dry_run=args.dry_run,
    )
    run = EvaluationRunner(project_root).run(
        configuration, FixtureBaselineExperiment()
    )
    print(run.output_dir)
    return 0 if run.summary["counts"]["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

