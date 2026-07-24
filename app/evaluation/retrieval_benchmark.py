"""고정 조건에서 검색 provider 조합별 성능을 비교한다."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from app.evaluation.metrics import (
    file_recall_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    reciprocal_rank,
)
from app.evaluation.case import EvaluationCase


@dataclass(frozen=True)
class RetrievalExperimentConfig:
    experiment_id: str
    enabled_sources: tuple[str, ...]
    top_k: int = 5
    scoring_version: str = "retrieval-scoring-v1"
    normalization_version: str = "retrieval-normalization-v1"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    relevant_files: tuple[str, ...]
    predicted_files: tuple[str, ...]
    duration_ms: int
    provider_failure_count: int = 0


@dataclass(frozen=True)
class BenchmarkResult:
    output_dir: Path
    summary: dict[str, dict]


def default_experiments() -> tuple[RetrievalExperimentConfig, ...]:
    return (
        RetrievalExperimentConfig("rag_only", ("rag",)),
        RetrievalExperimentConfig("rag_dict", ("rag", "term_dictionary")),
        RetrievalExperimentConfig("rag_const", ("rag", "constant_match")),
        RetrievalExperimentConfig(
            "hybrid_all", ("rag", "term_dictionary", "constant_match")
        ),
        RetrievalExperimentConfig(
            "verified_hybrid",
            ("verified_mapping", "rag", "term_dictionary", "constant_match"),
        ),
    )


class RetrievalBenchmark:
    """실제 provider 실행 함수와 보고서 계산을 분리한 비교 runner."""

    def __init__(
        self,
        run_variant: Callable[
            [RetrievalExperimentConfig], Sequence[BenchmarkCase]
        ],
        experiments: Sequence[RetrievalExperimentConfig] | None = None,
        environment_snapshot: dict | None = None,
    ) -> None:
        self.run_variant = run_variant
        self.experiments = tuple(experiments or default_experiments())
        self.environment_snapshot = dict(environment_snapshot or {})

    def run(self, result_root: Path, run_name: str) -> BenchmarkResult:
        output_dir = result_root.resolve() / run_name
        output_dir.mkdir(parents=True, exist_ok=False)
        summary: dict[str, dict] = {}
        ranks: dict[str, dict[str, float]] = {}
        for experiment in self.experiments:
            cases = tuple(self.run_variant(experiment))
            summary[experiment.experiment_id] = _aggregate(cases)
            for case in cases:
                ranks.setdefault(case.case_id, {})[experiment.experiment_id] = reciprocal_rank(
                    case.relevant_files, case.predicted_files
                )
        _write_json(output_dir / "comparison.json", summary)
        (output_dir / "comparison.md").write_text(
            _render_comparison(summary), encoding="utf-8"
        )
        rank_text = "".join(
            _compact_json({"case_id": case_id, "reciprocal_ranks": values}) + "\n"
            for case_id, values in sorted(ranks.items())
        )
        (output_dir / "case_ranks.jsonl").write_text(rank_text, encoding="utf-8")
        environment = {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "experiments": [
                {
                    "experiment_id": item.experiment_id,
                    "enabled_sources": list(item.enabled_sources),
                    "top_k": item.top_k,
                    "scoring_version": item.scoring_version,
                    "normalization_version": item.normalization_version,
                }
                for item in self.experiments
            ],
            **self.environment_snapshot,
        }
        _write_json(output_dir / "environment.json", environment)
        return BenchmarkResult(output_dir, summary)


def _aggregate(cases: Sequence[BenchmarkCase]) -> dict:
    count = len(cases)
    if not count:
        return {
            "case_count": 0, "recall_at_1": 0.0, "recall_at_5": 0.0,
            "mrr": 0.0, "precision_at_5": 0.0, "average_latency_ms": 0.0,
            "provider_failure_count": 0,
        }
    pairs = [(case.relevant_files, case.predicted_files) for case in cases]
    return {
        "case_count": count,
        "recall_at_1": sum(file_recall_at_k(*pair, 1) for pair in pairs) / count,
        "recall_at_5": sum(file_recall_at_k(*pair, 5) for pair in pairs) / count,
        "mrr": mean_reciprocal_rank(pairs),
        "precision_at_5": sum(precision_at_k(*pair, 5) for pair in pairs) / count,
        "average_latency_ms": sum(case.duration_ms for case in cases) / count,
        "provider_failure_count": sum(case.provider_failure_count for case in cases),
    }


def _render_comparison(summary: dict[str, dict]) -> str:
    lines = [
        "# Retrieval Ablation Comparison", "",
        "| Experiment | Recall@1 | Recall@5 | MRR | Precision@5 | Latency(ms) | Provider failures |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in summary.items():
        lines.append(
            f"| {name} | {metrics['recall_at_1']:.3f} | {metrics['recall_at_5']:.3f} | "
            f"{metrics['mrr']:.3f} | {metrics['precision_at_5']:.3f} | "
            f"{metrics['average_latency_ms']:.1f} | {metrics['provider_failure_count']} |"
        )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def run_orchestrator_cases(orchestrator, cases: Sequence[EvaluationCase], experiment) -> tuple[BenchmarkCase, ...]:
    """동일 case들을 지정 provider 조합으로 실행한다."""
    from app.domain.common.enums import RetrievalSource
    from app.retrieval.orchestrator import RetrievalConfig, RetrievalQuery

    enabled = frozenset(RetrievalSource(value) for value in experiment.enabled_sources)
    results = []
    for case in cases:
        if case.expected.retrieval is None:
            continue
        text = " ".join(
            filter(None, (case.law.law_name, case.law.article, case.law.before_text, case.law.after_text))
        )
        started = time.monotonic()
        response = orchestrator.retrieve(
            RetrievalQuery(text, domain=case.domain, top_k_per_provider=experiment.top_k),
            RetrievalConfig(enabled_sources=enabled, final_top_k=experiment.top_k),
        )
        failures = sum(status.status == "error" for status in response.provider_statuses.values())
        results.append(
            BenchmarkCase(
                case.case_id,
                case.expected.retrieval.relevant_files,
                tuple(dict.fromkeys(item.location.path for item in response.candidates)),
                int((time.monotonic() - started) * 1000),
                failures,
            )
        )
    return tuple(results)


def ensure_benchmark_index(indexer, adapter, build_index: bool) -> int:
    """전용 index가 비어 있으면 명시적 옵션에서만 fixture를 인덱싱한다."""
    count = indexer.collection.count()
    if count:
        return count
    if not build_index:
        raise RuntimeError(
            "benchmark index is empty; rerun with --build-index and a dedicated --persist-dir"
        )
    indexer.index(adapter)
    count = indexer.collection.count()
    if not count:
        raise RuntimeError("benchmark indexing completed but no chunks were created")
    return count


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run retrieval ablation benchmark")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--repo-root")
    parser.add_argument("--persist-dir", default="./chroma_data")
    parser.add_argument("--build-index", action="store_true")
    parser.add_argument("--refresh-caches", action="store_true")
    args = parser.parse_args(argv)

    from app.embedding.indexer import CodeIndexer
    from app.codebase.mock_adapter import MockCodebaseAdapter
    from app.evaluation.loader import DatasetLoader
    from app.retrieval.orchestrator import RetrievalOrchestrator
    from app.retrieval.providers import (
        ConstantProvider,
        DictionaryProvider,
        RagProvider,
        VerifiedMappingProvider,
    )
    from config import settings

    project_root = Path(__file__).resolve().parents[2]
    repo_root = args.repo_root or settings.repo_root or "mock_repo"
    cases = DatasetLoader(project_root, check_paths=True).load_yaml(args.dataset)
    indexer = CodeIndexer(args.persist_dir)
    adapter = MockCodebaseAdapter(repo_root=repo_root, indexer=indexer)
    index_count = ensure_benchmark_index(indexer, adapter, args.build_index)
    orchestrator = RetrievalOrchestrator(
        (
            VerifiedMappingProvider(lambda _query: ()),
            RagProvider(lambda text, k: indexer.search(text, k=k)),
            DictionaryProvider(repo_root, refresh_cache=args.refresh_caches),
            ConstantProvider(repo_root, refresh_cache=args.refresh_caches),
        )
    )
    benchmark = RetrievalBenchmark(
        lambda experiment: run_orchestrator_cases(orchestrator, cases, experiment),
        environment_snapshot={
            "embedding_model": settings.embedding_model,
            "index_chunk_count": index_count,
            "persist_dir": str(Path(args.persist_dir).resolve()),
            "repository_root": str(Path(repo_root).resolve()),
        },
    )
    result = benchmark.run(args.result_dir, args.run_name)
    print(result.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
