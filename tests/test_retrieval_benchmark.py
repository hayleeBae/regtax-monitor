"""Issue #0010 검색 조합 비교 benchmark 테스트."""

from pathlib import Path

from app.evaluation.retrieval_benchmark import (
    BenchmarkCase,
    RetrievalBenchmark,
    default_experiments,
    ensure_benchmark_index,
)


def _run_variant(experiment):
    predictions = {
        "rag_only": ("wrong.java", "A.java"),
        "rag_dict": ("A.java",),
        "rag_const": ("A.java", "B.java"),
        "hybrid_all": ("A.java", "B.java"),
        "verified_hybrid": ("A.java", "B.java"),
    }
    return (
        BenchmarkCase("case-1", ("A.java",), predictions[experiment.experiment_id], 10),
        BenchmarkCase("case-2", ("B.java",), predictions[experiment.experiment_id], 20),
    )


def test_default_experiments_have_fixed_provider_combinations() -> None:
    experiments = default_experiments()
    assert [item.experiment_id for item in experiments] == [
        "rag_only", "rag_dict", "rag_const", "hybrid_all", "verified_hybrid"
    ]
    assert experiments[0].enabled_sources == ("rag",)
    assert "verified_mapping" in experiments[-1].enabled_sources


def test_benchmark_writes_comparison_and_case_rank_differences(tmp_path: Path) -> None:
    result = RetrievalBenchmark(_run_variant).run(tmp_path, "test-benchmark")

    assert (result.output_dir / "comparison.json").is_file()
    report = (result.output_dir / "comparison.md").read_text(encoding="utf-8")
    assert "Recall@1" in report and "MRR" in report
    assert "rag_only" in report and "hybrid_all" in report
    assert result.summary["rag_only"]["recall_at_1"] < result.summary["rag_dict"]["recall_at_1"]
    rank_rows = (result.output_dir / "case_ranks.jsonl").read_text(encoding="utf-8")
    assert '"case_id":"case-1"' in rank_rows
    assert (result.output_dir / "environment.json").is_file()


def test_same_cases_produce_deterministic_comparison_files(tmp_path: Path) -> None:
    first = RetrievalBenchmark(_run_variant).run(tmp_path / "a", "stable")
    second = RetrievalBenchmark(_run_variant).run(tmp_path / "b", "stable")
    for name in ("comparison.json", "comparison.md", "case_ranks.jsonl"):
        assert (first.output_dir / name).read_bytes() == (second.output_dir / name).read_bytes()


def test_empty_benchmark_index_requires_explicit_build() -> None:
    class Collection:
        value = 0
        def count(self):
            return self.value
    class Indexer:
        collection = Collection()
        def index(self, adapter):
            self.collection.value = 3
            return 3
    indexer = Indexer()
    try:
        ensure_benchmark_index(indexer, object(), False)
    except RuntimeError as exc:
        assert "--build-index" in str(exc)
    else:
        raise AssertionError("empty index must require --build-index")
    assert ensure_benchmark_index(indexer, object(), True) == 3
