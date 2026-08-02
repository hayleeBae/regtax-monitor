"""Issue #0017 replay fixture 스키마 계약 테스트 — HISTORICAL_REPLAY_SPEC §3·§8."""

from __future__ import annotations

import dataclasses

import pytest

from app.evaluation import ExpectedReplacement, LawInput
from app.evaluation.replay import (
    REPLAY_SCHEMA_VERSION,
    ArtifactKind,
    PrivacyMode,
    ReplayExecution,
    ReplayFixture,
    ReplayRepository,
    ReplayScope,
    allowed_artifacts,
)

_METRIC_KINDS = {ArtifactKind.METRIC, ArtifactKind.HASH, ArtifactKind.COUNT}
_CODE_KINDS = {
    ArtifactKind.DIFF_BODY,
    ArtifactKind.CODE_SNIPPET,
    ArtifactKind.GOLDEN_OUTPUT,
}


def _law() -> LawInput:
    return LawInput(
        law_name="소득세법",
        tier="law",
        before_text="150000",
        after_text="250000",
        article="제59조의2",
    )


def _fixture(**kwargs: object) -> ReplayFixture:
    defaults: dict[str, object] = {
        "case_id": "historical_tax_2024_child_credit",
        "law": _law(),
        "repository": ReplayRepository(
            source_type="local_git",
            base_commit="case1/base",
            answer_commit="case1/answer",
            path="evaluation/fixtures/replay_repos/case1",
        ),
        "scope": ReplayScope(relevant_paths=("src/TaxService.java",)),
        "execution": ReplayExecution(),
    }
    defaults.update(kwargs)
    return ReplayFixture(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# allowed_artifacts (스펙 §8)
# ---------------------------------------------------------------------------


def test_full_allows_every_artifact_kind() -> None:
    assert allowed_artifacts(PrivacyMode.FULL) == frozenset(ArtifactKind)


def test_redacted_allows_structure_and_path_only() -> None:
    assert allowed_artifacts(PrivacyMode.REDACTED) == frozenset(
        {
            ArtifactKind.DIFF_STRUCTURE,
            ArtifactKind.FILE_PATH,
            ArtifactKind.METRIC,
            ArtifactKind.HASH,
            ArtifactKind.COUNT,
        }
    )


def test_metadata_only_allows_metric_hash_count_only() -> None:
    assert allowed_artifacts(PrivacyMode.METADATA_ONLY) == frozenset(
        _METRIC_KINDS
    )


@pytest.mark.parametrize(
    "kind",
    sorted(
        _CODE_KINDS | {ArtifactKind.FILE_PATH, ArtifactKind.DIFF_STRUCTURE},
        key=lambda k: k.value,
    ),
)
def test_metadata_only_never_leaks_code_or_paths(kind: ArtifactKind) -> None:
    """회귀 시 회사 코드가 디스크에 남는 지점 — 명시적으로 고정한다."""
    assert kind not in allowed_artifacts(PrivacyMode.METADATA_ONLY)


@pytest.mark.parametrize("kind", sorted(_CODE_KINDS, key=lambda k: k.value))
def test_redacted_never_stores_code_body(kind: ArtifactKind) -> None:
    assert kind not in allowed_artifacts(PrivacyMode.REDACTED)


@pytest.mark.parametrize(
    "kind", [ArtifactKind.DIFF_STRUCTURE, ArtifactKind.FILE_PATH]
)
def test_redacted_keeps_debuggable_structure(kind: ArtifactKind) -> None:
    """구조가 있어야 코드 본문 없이도 어긋난 지점을 특정할 수 있다."""
    assert kind in allowed_artifacts(PrivacyMode.REDACTED)


def test_allowed_artifacts_accepts_mode_value_string() -> None:
    assert allowed_artifacts(PrivacyMode("metadata_only")) == frozenset(
        _METRIC_KINDS
    )


# ---------------------------------------------------------------------------
# 기본값과 불변식 (스펙 §3)
# ---------------------------------------------------------------------------


def test_execution_defaults_to_metadata_only() -> None:
    execution = ReplayExecution()
    assert execution.privacy_mode is PrivacyMode.METADATA_ONLY
    assert execution.golden_command is None
    assert execution.timeout_seconds == 1800


@pytest.mark.parametrize("timeout", [0, -1])
def test_execution_rejects_non_positive_timeout(timeout: int) -> None:
    with pytest.raises(ValueError):
        ReplayExecution(timeout_seconds=timeout)


@pytest.mark.parametrize("case_id", ["", "   "])
def test_fixture_rejects_empty_case_id(case_id: str) -> None:
    with pytest.raises(ValueError):
        _fixture(case_id=case_id)


@pytest.mark.parametrize("timeout", [0, -30])
def test_fixture_rejects_non_positive_timeout(timeout: int) -> None:
    with pytest.raises(ValueError):
        _fixture(execution=ReplayExecution(timeout_seconds=timeout))


def test_fixture_defaults() -> None:
    fixture = _fixture()
    assert fixture.reviewed is False
    assert fixture.schema_version == REPLAY_SCHEMA_VERSION == "1"
    assert fixture.scope.excluded_paths == ()
    assert fixture.scope.expected_replacements == ()


def test_scope_reuses_expected_replacement_type() -> None:
    replacement = ExpectedReplacement(
        path="src/TaxService.java", before="150000", after="250000"
    )
    scope = ReplayScope(
        relevant_paths=("src/TaxService.java",),
        expected_replacements=(replacement,),
    )
    assert scope.expected_replacements[0] is replacement


def test_repository_path_and_path_env_are_not_validated_here() -> None:
    """path XOR path_env 검증은 로더(Step 1) 책임 — dataclass 는 던지지 않는다."""
    repository = ReplayRepository(
        source_type="local_git",
        base_commit="abc123",
        answer_commit="def456",
    )
    assert repository.path is None
    assert repository.path_env is None


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


def test_fixture_types_are_frozen() -> None:
    fixture = _fixture()
    with pytest.raises(dataclasses.FrozenInstanceError):
        fixture.case_id = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fixture.execution.privacy_mode = PrivacyMode.FULL  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fixture.repository.base_commit = "zzz"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fixture.scope.relevant_paths = ()  # type: ignore[misc]
