"""Issue #0004 — Evaluation Dataset & Metrics 단위 테스트.

네트워크, LLM, ChromaDB, FastAPI 없이 동작한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.evaluation import (
    DatasetLoader,
    DatasetValidationError,
    EvaluationCase,
)
from app.evaluation.metrics import (
    accuracy,
    aggregate_recall_at_k,
    case_hit_at_k,
    file_coverage,
    file_recall_at_k,
    macro_f1,
    mean_reciprocal_rank,
    patch_replacement_rate,
    per_class_metrics,
    precision_at_k,
    reciprocal_rank,
    unnecessary_file_rate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "core.yaml"


# ===========================================================================
# 1. schema 로드 — core.yaml 전체 케이스 로드
# ===========================================================================


def test_core_dataset_loads_all_cases():
    loader = DatasetLoader()
    cases = loader.load_yaml(CORE_DATASET)
    assert len(cases) >= 10, f"최소 10개 케이스 필요, 실제: {len(cases)}"
    for case in cases:
        assert isinstance(case, EvaluationCase)
        assert case.case_id
        assert case.domain in ("tax", "hr")


def test_core_dataset_case_ids_unique():
    loader = DatasetLoader()
    cases = loader.load_yaml(CORE_DATASET)
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_core_dataset_change_types_valid():
    loader = DatasetLoader()
    cases = loader.load_yaml(CORE_DATASET)
    valid = {
        "value_change", "rate_change", "date_change", "condition_change",
        "table_change", "new_field", "structural_change", "no_code_impact", "unknown",
    }
    for case in cases:
        assert case.expected.change_type in valid, (
            f"{case.case_id}: invalid change_type {case.expected.change_type!r}"
        )


def test_core_dataset_covers_required_types():
    loader = DatasetLoader()
    cases = loader.load_yaml(CORE_DATASET)
    types = {c.expected.change_type for c in cases}
    # spec §16 최소 유형
    assert "value_change" in types
    assert "rate_change" in types
    assert "date_change" in types


def test_case_with_patch_has_replacements():
    loader = DatasetLoader()
    cases = loader.load_yaml(CORE_DATASET)
    patch_cases = [c for c in cases if c.execution.evaluate_patch]
    assert patch_cases, "evaluate_patch=true 케이스가 있어야 한다"
    for case in patch_cases:
        assert case.expected.patch is not None
        assert len(case.expected.patch.expected_replacements) >= 1


# ===========================================================================
# 2. 스키마 검증 — 오류 케이스
# ===========================================================================


def _make_minimal_raw(**overrides) -> dict:
    """최소 유효 케이스 dict (인라인 오버라이드)."""
    base = {
        "schema_version": "1",
        "case_id": "test_case_001",
        "title": "Test Case",
        "domain": "tax",
        "tags": [],
        "law": {
            "law_name": "소득세법",
            "tier": "law",
            "before_text": "이전",
            "after_text": "이후",
        },
        "expected": {"change_type": "value_change"},
        "repository": {
            "fixture_type": "directory",
            "path": "evaluation/fixtures/repositories/mock_tax",
        },
        "execution": {},
        "metadata": {"source": "synthetic"},
    }
    base.update(overrides)
    return base


def test_loader_raises_on_missing_file():
    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError, match="not found"):
        loader.load_yaml("/tmp/nonexistent_dataset_xyz.yaml")


def test_loader_duplicate_case_id(tmp_path):
    raw = {
        "cases": [
            _make_minimal_raw(case_id="dup_001"),
            _make_minimal_raw(case_id="dup_001"),
        ]
    }
    f = tmp_path / "dup.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert any("duplicate" in d.lower() for d in exc_info.value.details)


def test_loader_invalid_change_type(tmp_path):
    raw = {"cases": [_make_minimal_raw(expected={"change_type": "invalid_xyz"})]}
    f = tmp_path / "bad_type.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert "change_type" in str(exc_info.value.details[0]).lower()


def test_loader_invalid_tier(tmp_path):
    law = {
        "law_name": "소득세법",
        "tier": "invalid_tier",
        "before_text": "a",
        "after_text": "b",
    }
    raw = {"cases": [_make_minimal_raw(law=law)]}
    f = tmp_path / "bad_tier.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert "tier" in str(exc_info.value.details[0]).lower()


def test_loader_path_traversal_rejected(tmp_path):
    repo = {
        "fixture_type": "directory",
        "path": "../../etc/passwd",
    }
    raw = {"cases": [_make_minimal_raw(repository=repo)]}
    f = tmp_path / "traversal.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert ".." in str(exc_info.value.details[0])


def test_loader_timeout_out_of_range(tmp_path):
    raw = {
        "cases": [
            _make_minimal_raw(
                execution={
                    "evaluate_classification": True,
                    "evaluate_retrieval": True,
                    "timeout_seconds": 0,
                }
            )
        ]
    }
    f = tmp_path / "bad_timeout.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert "timeout" in str(exc_info.value.details[0]).lower()


def test_loader_relevant_forbidden_overlap(tmp_path):
    expected = {
        "change_type": "value_change",
        "retrieval": {
            "relevant_files": ["src/A.java"],
        },
        "patch": {
            "expected_replacements": [
                {"path": "src/A.java", "before": "x", "after": "y"}
            ],
            "forbidden_files": ["src/A.java"],
        },
    }
    raw = {"cases": [_make_minimal_raw(expected=expected)]}
    f = tmp_path / "overlap.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert "forbidden" in str(exc_info.value.details[0]).lower()


def test_loader_check_paths_validates_existence(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "Exists.java").write_text("// ok")

    expected = {
        "change_type": "value_change",
        "retrieval": {
            "relevant_files": ["src/Exists.java", "src/Missing.java"],
        },
    }
    raw = {
        "cases": [
            _make_minimal_raw(
                repository={
                    "fixture_type": "directory",
                    "path": str(repo_dir.relative_to(tmp_path)),
                },
                expected=expected,
            )
        ]
    }
    f = tmp_path / "paths.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader(root_dir=tmp_path, check_paths=True)
    with pytest.raises(DatasetValidationError) as exc_info:
        loader.load_yaml(f)
    assert "Missing.java" in str(exc_info.value.details)


def test_loader_jsonl_format(tmp_path):
    case1 = _make_minimal_raw(case_id="jsonl_001")
    case2 = _make_minimal_raw(case_id="jsonl_002")
    import json

    f = tmp_path / "cases.jsonl"
    f.write_text(
        json.dumps(case1, ensure_ascii=False) + "\n"
        + json.dumps(case2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    loader = DatasetLoader()
    cases = loader.load_jsonl(f)
    assert len(cases) == 2
    assert {c.case_id for c in cases} == {"jsonl_001", "jsonl_002"}


def test_loader_single_case_yaml(tmp_path):
    raw = _make_minimal_raw(case_id="single_001")
    f = tmp_path / "single.yaml"
    f.write_text(yaml.dump(raw, allow_unicode=True), encoding="utf-8")

    loader = DatasetLoader()
    cases = loader.load_yaml(f)
    assert len(cases) == 1
    assert cases[0].case_id == "single_001"


# ===========================================================================
# 3. Recall@K
# ===========================================================================


def test_case_hit_at_k_returns_true_when_relevant_in_top_k():
    relevant = ["a.java", "b.java"]
    predicted = ["x.java", "a.java", "z.java"]
    assert case_hit_at_k(relevant, predicted, k=2) is True
    assert case_hit_at_k(relevant, predicted, k=1) is False


def test_case_hit_at_k_empty_relevant():
    # 정답이 없으면 항상 True
    assert case_hit_at_k([], ["x.java"], k=3) is True


def test_file_recall_at_k_basic():
    relevant = ["a.java", "b.java", "c.java"]
    predicted = ["a.java", "x.java", "b.java", "c.java"]
    assert file_recall_at_k(relevant, predicted, k=3) == pytest.approx(2 / 3)
    assert file_recall_at_k(relevant, predicted, k=4) == pytest.approx(1.0)


def test_file_recall_at_k_empty_predicted():
    assert file_recall_at_k(["a.java"], [], k=5) == pytest.approx(0.0)


def test_file_recall_at_k_empty_relevant():
    assert file_recall_at_k([], ["a.java"], k=5) == pytest.approx(1.0)


def test_precision_at_k_basic():
    relevant = ["a.java", "b.java"]
    predicted = ["a.java", "x.java", "b.java"]
    assert precision_at_k(relevant, predicted, k=2) == pytest.approx(1 / 2)
    assert precision_at_k(relevant, predicted, k=3) == pytest.approx(2 / 3)


def test_precision_at_k_empty_predicted():
    assert precision_at_k(["a.java"], [], k=5) == pytest.approx(0.0)


def test_aggregate_recall_at_k_multiple_cases():
    cases = [
        (["a.java"], ["a.java", "b.java"]),
        (["x.java"], ["y.java", "z.java"]),
    ]
    result = aggregate_recall_at_k(cases, k=1)
    assert result["hit_rate"] == pytest.approx(0.5)
    assert result["file_recall"] == pytest.approx(0.5)


def test_aggregate_recall_at_k_empty():
    result = aggregate_recall_at_k([], k=5)
    assert result["hit_rate"] == 0.0


# ===========================================================================
# 4. MRR
# ===========================================================================


def test_reciprocal_rank_first_position():
    assert reciprocal_rank(["a.java"], ["a.java", "b.java"]) == pytest.approx(1.0)


def test_reciprocal_rank_second_position():
    assert reciprocal_rank(["b.java"], ["a.java", "b.java", "c.java"]) == pytest.approx(0.5)


def test_reciprocal_rank_not_found():
    assert reciprocal_rank(["z.java"], ["a.java", "b.java"]) == pytest.approx(0.0)


def test_reciprocal_rank_empty_predicted():
    assert reciprocal_rank(["a.java"], []) == pytest.approx(0.0)


def test_mean_reciprocal_rank_basic():
    cases = [
        (["a.java"], ["a.java"]),         # rank 1 → 1.0
        (["b.java"], ["x.java", "b.java"]), # rank 2 → 0.5
    ]
    assert mean_reciprocal_rank(cases) == pytest.approx(0.75)


def test_mean_reciprocal_rank_empty():
    assert mean_reciprocal_rank([]) == pytest.approx(0.0)


def test_mean_reciprocal_rank_duplicate_candidates():
    # 예측 목록에 중복 포함 → 첫 등장 위치 기준
    relevant = ["a.java"]
    predicted = ["x.java", "a.java", "a.java"]
    assert reciprocal_rank(relevant, predicted) == pytest.approx(0.5)


# ===========================================================================
# 5. Macro F1
# ===========================================================================


def test_accuracy_perfect():
    preds = ["value_change", "rate_change"]
    labels = ["value_change", "rate_change"]
    assert accuracy(preds, labels) == pytest.approx(1.0)


def test_accuracy_partial():
    preds = ["value_change", "rate_change", "date_change"]
    labels = ["value_change", "rate_change", "value_change"]
    assert accuracy(preds, labels) == pytest.approx(2 / 3)


def test_accuracy_empty():
    assert accuracy([], []) == pytest.approx(0.0)


def test_per_class_metrics_basic():
    preds = ["value_change", "rate_change", "value_change", "date_change"]
    labels = ["value_change", "value_change", "rate_change", "date_change"]
    result = per_class_metrics(preds, labels)
    # value_change: TP=1, FP=1, FN=1
    assert result["value_change"]["precision"] == pytest.approx(0.5)
    assert result["value_change"]["recall"] == pytest.approx(0.5)
    assert result["value_change"]["f1"] == pytest.approx(0.5)
    # date_change: TP=1, FP=0, FN=0 → perfect
    assert result["date_change"]["f1"] == pytest.approx(1.0)


def test_macro_f1_perfect_classifier():
    preds = labels = ["value_change", "rate_change", "date_change"]
    assert macro_f1(preds, labels) == pytest.approx(1.0)


def test_macro_f1_empty():
    assert macro_f1([], []) == pytest.approx(0.0)


def test_macro_f1_uses_only_label_classes():
    # 정답에 없는 클래스를 예측해도 macro 분모에 포함되지 않는다
    preds = ["value_change", "unknown"]
    labels = ["value_change", "value_change"]
    result = macro_f1(preds, labels)
    # value_change: TP=1, FP=0, FN=1 → recall=0.5, prec=1.0, f1=0.667
    assert 0 < result <= 1.0


# ===========================================================================
# 6. Patch Replacement Rate
# ===========================================================================


def test_patch_replacement_rate_all_matched():
    expected = [("150000L", "250000L"), ("9000000L", "10000000L")]
    actual = [("9000000L", "10000000L"), ("150000L", "250000L"), ("extra", "extra")]
    assert patch_replacement_rate(expected, actual) == pytest.approx(1.0)


def test_patch_replacement_rate_partial():
    expected = [("150000L", "250000L"), ("9000000L", "10000000L")]
    actual = [("150000L", "250000L")]
    assert patch_replacement_rate(expected, actual) == pytest.approx(0.5)


def test_patch_replacement_rate_empty_expected():
    assert patch_replacement_rate([], [("a", "b")]) == pytest.approx(1.0)


def test_patch_replacement_rate_empty_prediction():
    expected = [("150000L", "250000L")]
    assert patch_replacement_rate(expected, []) == pytest.approx(0.0)


# ===========================================================================
# 7. File Coverage / Unnecessary File Rate
# ===========================================================================


def test_file_coverage_all_covered():
    relevant = ["a.java", "b.java"]
    patched = ["a.java", "b.java", "c.java"]
    assert file_coverage(relevant, patched) == pytest.approx(1.0)


def test_file_coverage_partial():
    relevant = ["a.java", "b.java", "c.java"]
    patched = ["a.java"]
    assert file_coverage(relevant, patched) == pytest.approx(1 / 3)


def test_file_coverage_empty_relevant():
    assert file_coverage([], ["a.java"]) == pytest.approx(1.0)


def test_unnecessary_file_rate_no_unnecessary():
    patched = ["a.java", "b.java"]
    relevant = ["a.java", "b.java", "c.java"]
    assert unnecessary_file_rate(patched, relevant) == pytest.approx(0.0)


def test_unnecessary_file_rate_all_unnecessary():
    patched = ["x.java", "y.java"]
    relevant = ["a.java", "b.java"]
    assert unnecessary_file_rate(patched, relevant) == pytest.approx(1.0)


def test_unnecessary_file_rate_mixed():
    patched = ["a.java", "x.java"]
    relevant = ["a.java", "b.java"]
    assert unnecessary_file_rate(patched, relevant) == pytest.approx(0.5)


def test_unnecessary_file_rate_empty_patched():
    assert unnecessary_file_rate([], ["a.java"]) == pytest.approx(0.0)


# ===========================================================================
# 8. 계층 제약 — evaluation 모듈이 금지 패키지를 import 하지 않는다
# ===========================================================================


def test_evaluation_layer_has_no_forbidden_imports():
    forbidden = ("fastapi", "sqlalchemy", "anthropic", "app.main", "app.db", "app.llm")
    eval_dir = PROJECT_ROOT / "app" / "evaluation"
    py_files = list(eval_dir.rglob("*.py"))
    assert py_files, "evaluation 소스가 존재해야 한다"
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert (
                f"import {token}" not in text and f"from {token}" not in text
            ), f"{path.name} must not import {token}"
