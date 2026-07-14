"""Issue #0003 — V2 Foundation Contracts 단위 테스트.

순수 domain 계층만 검증한다. 무거운 의존성(임베딩/Chroma/LLM)을 건드리지 않는다.
domain 계층이 FastAPI/SQLAlchemy/외부 LLM SDK 를 import 하지 않는다는 제약도 함께 고정한다.
"""

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domain.common import (
    AutomationDecision,
    ChangeType,
    DomainError,
    ErrorCategory,
    RetrievalSource,
    RunStatus,
    RunType,
    VersionedComponent,
    to_jsonable,
)
from app.domain.runs import RunContext, new_run_id, utc_now

ROOT = Path(__file__).resolve().parents[1]


# --- enum 값·직렬화 -------------------------------------------------------


def test_change_type_values_match_spec():
    # ARCHITECTURE_V2 §5.2 / CHANGE_CLASSIFICATION_SPEC §2 와 정확히 일치해야 한다.
    assert {c.value for c in ChangeType} == {
        "value_change",
        "rate_change",
        "date_change",
        "condition_change",
        "table_change",
        "new_field",
        "structural_change",
        "no_code_impact",
        "unknown",
    }


def test_automation_decision_values_match_spec():
    assert {d.value for d in AutomationDecision} == {
        "draft_allowed",
        "analysis_only",
        "manual_review_required",
    }


def test_error_category_values_match_spec():
    # ARCHITECTURE_V2 §11 목록.
    assert {e.value for e in ErrorCategory} == {
        "input_error",
        "source_unavailable",
        "classification_error",
        "retrieval_error",
        "policy_blocked",
        "llm_error",
        "anchor_error",
        "patch_error",
        "golden_test_error",
        "audit_error",
        "evaluation_error",
    }


def test_retrieval_source_values_match_spec():
    assert {s.value for s in RetrievalSource} == {
        "verified_mapping",
        "rag",
        "term_dictionary",
        "constant_match",
        "code_graph",
        "historical_commit",
    }


def test_run_type_and_status_values():
    assert {r.value for r in RunType} == {"production", "evaluation", "replay"}
    assert {s.value for s in RunStatus} == {
        "created",
        "running",
        "completed",
        "failed",
    }


def test_enum_serializes_to_value_string():
    assert to_jsonable(ChangeType.VALUE_CHANGE) == "value_change"
    assert to_jsonable(AutomationDecision.DRAFT_ALLOWED) == "draft_allowed"
    # str, Enum 파생이므로 json.dumps 도 값 문자열로 직렬화된다.
    assert json.loads(json.dumps(to_jsonable(RunType.PRODUCTION))) == "production"


# --- VersionedComponent ---------------------------------------------------


def test_versioned_component_valid():
    vc = VersionedComponent(name="rule-classifier", version="v1")
    assert vc.label == "rule-classifier-v1"
    assert vc.to_dict() == {"name": "rule-classifier", "version": "v1"}
    assert to_jsonable(vc) == {"name": "rule-classifier", "version": "v1"}


@pytest.mark.parametrize("bad", ["", " ", "v 1", "bad/slash", "v@1", "-v1", "v1-"])
def test_versioned_component_invalid_version_raises(bad):
    with pytest.raises(ValueError):
        VersionedComponent(name="x", version=bad)


def test_versioned_component_empty_name_raises():
    with pytest.raises(ValueError):
        VersionedComponent(name="  ", version="v1")


def test_versioned_component_is_frozen():
    vc = VersionedComponent(name="edit", version="v2")
    with pytest.raises(dataclasses.FrozenInstanceError):
        vc.version = "v3"  # type: ignore[misc]


# --- run_id / 시간 유틸 ----------------------------------------------------


def test_new_run_id_prefix_and_uniqueness():
    ids = {new_run_id() for _ in range(2000)}
    assert len(ids) == 2000  # 유일성
    assert all(rid.startswith("run_") for rid in ids)


def test_utc_now_is_timezone_aware():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


# --- RunContext -----------------------------------------------------------


def test_run_context_requires_run_id():
    with pytest.raises(ValueError):
        RunContext(run_id="", run_type=RunType.PRODUCTION)


def test_run_context_is_frozen():
    ctx = RunContext(run_id=new_run_id(), run_type=RunType.PRODUCTION)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.status = RunStatus.RUNNING  # type: ignore[misc]


def test_run_context_prompt_versions_immutable():
    ctx = RunContext(
        run_id=new_run_id(),
        run_type=RunType.PRODUCTION,
        prompt_versions={"analysis": "analysis-v2"},
    )
    with pytest.raises(TypeError):
        ctx.prompt_versions["analysis"] = "hacked"  # type: ignore[index]


def test_run_context_source_dict_copied_not_referenced():
    src = {"analysis": "analysis-v2"}
    ctx = RunContext(
        run_id=new_run_id(), run_type=RunType.PRODUCTION, prompt_versions=src
    )
    src["analysis"] = "mutated"  # 외부 dict 변경이 컨텍스트에 새어들면 안 된다.
    assert ctx.prompt_versions["analysis"] == "analysis-v2"


def test_run_context_transitions_return_new_instances():
    ctx = RunContext(run_id=new_run_id(), run_type=RunType.PRODUCTION)
    t0 = datetime(2026, 7, 14, 1, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 14, 2, 0, 0, tzinfo=timezone.utc)

    running = ctx.start(now=t0)
    done = running.complete(now=t1)

    # 원본 불변
    assert ctx.status is RunStatus.CREATED
    assert ctx.started_at is None
    # 전이 결과
    assert running.status is RunStatus.RUNNING
    assert running.started_at == t0
    assert done.status is RunStatus.COMPLETED
    assert done.started_at == t0
    assert done.completed_at == t1

    failed = running.fail(now=t1)
    assert failed.status is RunStatus.FAILED
    assert failed.completed_at == t1


def test_run_context_json_serialization():
    t0 = datetime(2026, 7, 14, 1, 0, 0, tzinfo=timezone.utc)
    ctx = RunContext(
        run_id="run_fixedid",
        run_type=RunType.PRODUCTION,
        law_change_id=123,
        repository_commit="abc123",
        embedding_model="BAAI/bge-m3",
        llm_backend="local",
        llm_model="qwen3:8b",
        prompt_versions={"analysis": "analysis-v2"},
        started_at=t0,
    ).with_status(RunStatus.RUNNING)

    data = ctx.to_dict()
    # json.dumps 가능해야 한다(datetime/enum 가 문자열로 변환됨).
    dumped = json.dumps(data)
    restored = json.loads(dumped)

    assert restored["run_id"] == "run_fixedid"
    assert restored["run_type"] == "production"
    assert restored["status"] == "running"
    assert restored["law_change_id"] == 123
    assert restored["prompt_versions"] == {"analysis": "analysis-v2"}
    assert restored["started_at"].startswith("2026-07-14T01:00:00")
    assert restored["completed_at"] is None


def test_naive_datetime_serialized_as_utc():
    naive = datetime(2026, 7, 14, 1, 0, 0)
    out = to_jsonable(naive)
    assert out.endswith("+00:00")


# --- DomainError ----------------------------------------------------------


def test_domain_error_carries_category_and_hides_internal_detail():
    err = DomainError(
        ErrorCategory.LLM_ERROR,
        "분류에 실패했습니다.",
        retryable=True,
        internal_detail="secret-ish trace",
    )
    assert err.category is ErrorCategory.LLM_ERROR
    assert err.retryable is True
    safe = err.to_dict()
    assert safe == {
        "category": "llm_error",
        "message": "분류에 실패했습니다.",
        "retryable": True,
    }
    assert "internal_detail" not in safe


# --- 계층 제약 (ARCHITECTURE_V2 §3 / roadmap #0003) ------------------------


def test_domain_layer_has_no_forbidden_imports():
    """domain 모듈이 FastAPI/SQLAlchemy/외부 LLM SDK/app.main 을 import 하지 않는다."""
    forbidden = ("fastapi", "sqlalchemy", "anthropic", "app.main", "app.db", "app.llm")
    domain_dir = ROOT / "app" / "domain"
    py_files = list(domain_dir.rglob("*.py"))
    assert py_files, "domain 소스가 존재해야 한다"
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert (
                f"import {token}" not in text and f"from {token}" not in text
            ), f"{path.name} must not import {token}"
