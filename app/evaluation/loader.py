"""DatasetLoader — YAML/JSONL 데이터셋 로더 및 유효성 검사.

EVALUATION_SPEC.md §3 / §5 에 정의된 규칙을 구현한다.
네트워크와 LLM 없이 동작한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

import yaml

from app.evaluation.case import (
    CaseMetadata,
    EvaluationCase,
    ExecutionExpectation,
    ExpectedOutcome,
    ExpectedPatch,
    ExpectedReplacement,
    ExpectedRetrieval,
    LawInput,
    RepositoryFixture,
)
from app.evaluation.errors import DatasetValidationError

# ---------------------------------------------------------------------------
# 허용 값 상수
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1"})
SUPPORTED_TIERS: frozenset[str] = frozenset(
    {"law", "enforcement_decree", "enforcement_rule", "admin_rule"}
)
SUPPORTED_MATCH_MODES: frozenset[str] = frozenset({"exact", "normalized_text"})
VALID_CHANGE_TYPES: frozenset[str] = frozenset(
    {
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
)
VALID_AUTOMATION_DECISIONS: frozenset[str] = frozenset(
    {"draft_allowed", "analysis_only", "manual_review_required"}
)
VALID_FIXTURE_TYPES: frozenset[str] = frozenset({"directory", "git_commit"})
VALID_SOURCES: frozenset[str] = frozenset({"synthetic", "historical", "real"})
MIN_TIMEOUT: int = 1
MAX_TIMEOUT: int = 3600


# ---------------------------------------------------------------------------
# DatasetLoader
# ---------------------------------------------------------------------------


class DatasetLoader:
    """평가 데이터셋을 로드하고 유효성을 검사한다.

    check_paths=True 이고 root_dir 이 제공되면 fixture 경로와
    예상 파일의 존재를 검사한다. check_paths=False 이면 경로 존재
    검사를 생략한다 (단위 테스트용).
    """

    def __init__(
        self,
        root_dir: Optional[Union[str, Path]] = None,
        check_paths: bool = False,
    ) -> None:
        self._root_dir: Optional[Path] = Path(root_dir) if root_dir else None
        self._check_paths = check_paths

    # ------------------------------------------------------------------
    # 공개 진입점
    # ------------------------------------------------------------------

    def load_yaml(self, path: Union[str, Path]) -> list[EvaluationCase]:
        """YAML 파일에서 케이스 목록을 로드한다.

        지원 형식:
        - 케이스 dict 하나 (단일 케이스)
        - 케이스 dict 목록 (list)
        - ``cases:`` 키를 가진 dict (데이터셋 파일)
        """
        path = Path(path)
        if not path.exists():
            raise DatasetValidationError(f"Dataset file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise DatasetValidationError(
                f"YAML parse error in {path.name}: {exc}"
            ) from exc

        if isinstance(raw, dict) and "cases" in raw:
            raw_cases = raw["cases"]
        elif isinstance(raw, list):
            raw_cases = raw
        elif isinstance(raw, dict):
            raw_cases = [raw]
        else:
            raise DatasetValidationError(
                f"Unsupported YAML structure in {path.name}: expected dict or list"
            )

        return self._load_and_validate(raw_cases, source_path=path)

    def load_jsonl(self, path: Union[str, Path]) -> list[EvaluationCase]:
        """JSONL 파일에서 케이스 목록을 로드한다 (한 줄 = 케이스 하나)."""
        path = Path(path)
        if not path.exists():
            raise DatasetValidationError(f"Dataset file not found: {path}")

        raw_cases: list[dict] = []
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw_cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DatasetValidationError(
                    f"Invalid JSON on line {lineno} of {path.name}: {exc}"
                ) from exc

        return self._load_and_validate(raw_cases, source_path=path)

    # ------------------------------------------------------------------
    # 내부 로직
    # ------------------------------------------------------------------

    def _load_and_validate(
        self,
        raw_cases: list,
        source_path: Optional[Path] = None,
    ) -> list[EvaluationCase]:
        if not isinstance(raw_cases, list):
            raise DatasetValidationError("Expected a list of case dicts")

        errors: list[str] = []
        seen_ids: set[str] = set()
        cases: list[EvaluationCase] = []

        for i, raw in enumerate(raw_cases):
            if not isinstance(raw, dict):
                errors.append(f"[index {i}] expected dict, got {type(raw).__name__}")
                continue

            case_id = str(raw.get("case_id", f"<index {i}>"))
            case_errors: list[str] = []

            # schema_version
            sv = str(raw.get("schema_version", "1"))
            if sv not in SUPPORTED_SCHEMA_VERSIONS:
                case_errors.append(f"unsupported schema_version: {sv!r}")

            # duplicate case_id
            if case_id in seen_ids:
                case_errors.append(f"duplicate case_id: {case_id!r}")
            seen_ids.add(case_id)

            try:
                case = self._parse_case(raw, case_errors)
            except Exception as exc:
                case_errors.append(f"parse error: {exc}")
                errors.extend(f"[{case_id}] {e}" for e in case_errors)
                continue

            # path existence check (optional)
            if self._check_paths and self._root_dir is not None:
                self._validate_paths(case, case_errors)

            if case_errors:
                errors.extend(f"[{case_id}] {e}" for e in case_errors)
            else:
                cases.append(case)

        if errors:
            raise DatasetValidationError(
                f"Dataset validation failed ({len(errors)} error(s))",
                details=errors,
            )
        return cases

    # ------------------------------------------------------------------
    # 케이스 파싱
    # ------------------------------------------------------------------

    def _parse_case(self, raw: dict, errors: list[str]) -> EvaluationCase:
        case_id = str(raw.get("case_id", ""))
        if not case_id:
            errors.append("case_id is required")

        domain = str(raw.get("domain", ""))
        if not domain:
            errors.append("domain is required")

        title = str(raw.get("title", ""))
        tags = tuple(str(t) for t in raw.get("tags", []))
        schema_version = str(raw.get("schema_version", "1"))

        law = self._parse_law(raw.get("law") or {}, errors)
        expected = self._parse_expected(raw.get("expected") or {}, errors)
        repository = self._parse_repository(raw.get("repository") or {}, errors)
        execution = self._parse_execution(raw.get("execution") or {}, errors)
        metadata = self._parse_metadata(
            raw.get("metadata") or {}, schema_version, errors
        )

        # relevant/forbidden 중복 검사
        if expected.retrieval and expected.patch:
            rel_set = set(expected.retrieval.relevant_files)
            forb_set = set(expected.patch.forbidden_files)
            overlap = rel_set & forb_set
            if overlap:
                errors.append(
                    f"files appear in both relevant_files and forbidden_files: {sorted(overlap)}"
                )

        return EvaluationCase(
            case_id=case_id,
            title=title,
            domain=domain,
            tags=tags,
            law=law,
            expected=expected,
            repository=repository,
            execution=execution,
            metadata=metadata,
        )

    def _parse_law(self, raw: dict, errors: list[str]) -> LawInput:
        law_name = str(raw.get("law_name", ""))
        if not law_name:
            errors.append("law.law_name is required")

        tier = str(raw.get("tier", "law"))
        if tier not in SUPPORTED_TIERS:
            errors.append(f"law.tier invalid: {tier!r}")

        return LawInput(
            law_name=law_name,
            tier=tier,
            before_text=str(raw.get("before_text", "")),
            after_text=str(raw.get("after_text", "")),
            article=raw.get("article"),
            effective_date=raw.get("effective_date"),
        )

    def _parse_expected(self, raw: dict, errors: list[str]) -> ExpectedOutcome:
        change_type = str(raw.get("change_type", ""))
        if change_type not in VALID_CHANGE_TYPES:
            errors.append(f"expected.change_type invalid: {change_type!r}")

        automation_decision = raw.get("automation_decision")
        if (
            automation_decision is not None
            and automation_decision not in VALID_AUTOMATION_DECISIONS
        ):
            errors.append(
                f"expected.automation_decision invalid: {automation_decision!r}"
            )

        retrieval: Optional[ExpectedRetrieval] = None
        r_raw = raw.get("retrieval")
        if r_raw is not None:
            retrieval = ExpectedRetrieval(
                relevant_files=tuple(r_raw.get("relevant_files") or []),
                primary_files=tuple(r_raw.get("primary_files") or []),
                relevant_symbols=tuple(r_raw.get("relevant_symbols") or []),
            )

        patch: Optional[ExpectedPatch] = None
        p_raw = raw.get("patch")
        if p_raw is not None:
            replacements: list[ExpectedReplacement] = []
            for rep in p_raw.get("expected_replacements") or []:
                mm = rep.get("match_mode", "exact")
                if mm not in SUPPORTED_MATCH_MODES:
                    errors.append(f"patch.match_mode invalid: {mm!r}")
                replacements.append(
                    ExpectedReplacement(
                        path=str(rep.get("path", "")),
                        before=str(rep.get("before", "")),
                        after=str(rep.get("after", "")),
                        match_mode=mm,
                    )
                )
            patch = ExpectedPatch(
                expected_replacements=tuple(replacements),
                forbidden_files=tuple(p_raw.get("forbidden_files") or []),
                require_git_apply=bool(p_raw.get("require_git_apply", False)),
                require_golden_pass=bool(p_raw.get("require_golden_pass", False)),
            )

        return ExpectedOutcome(
            change_type=change_type,
            automation_decision=automation_decision,
            retrieval=retrieval,
            patch=patch,
        )

    def _parse_repository(self, raw: dict, errors: list[str]) -> RepositoryFixture:
        fixture_type = str(raw.get("fixture_type", "directory"))
        if fixture_type not in VALID_FIXTURE_TYPES:
            errors.append(f"repository.fixture_type invalid: {fixture_type!r}")

        path = str(raw.get("path", ""))
        if not path:
            errors.append("repository.path is required")
        else:
            # path traversal 방지 — ".." 구성 요소 검사
            if ".." in Path(path).parts:
                errors.append(
                    f"repository.path must not contain '..': {path!r}"
                )

        return RepositoryFixture(
            fixture_type=fixture_type,
            path=path,
            base_commit=raw.get("base_commit"),
            answer_commit=raw.get("answer_commit"),
            golden_command=raw.get("golden_command"),
        )

    def _parse_execution(self, raw: dict, errors: list[str]) -> ExecutionExpectation:
        timeout = int(raw.get("timeout_seconds", 600))
        if not (MIN_TIMEOUT <= timeout <= MAX_TIMEOUT):
            errors.append(
                f"execution.timeout_seconds out of range "
                f"[{MIN_TIMEOUT}, {MAX_TIMEOUT}]: {timeout}"
            )

        top_k_raw = raw.get("top_k") or [1, 3, 5, 10]
        top_k_list: list[int] = []
        for k in top_k_raw:
            try:
                kv = int(k)
            except (TypeError, ValueError):
                errors.append(f"execution.top_k value must be integer: {k!r}")
                kv = 1
            if kv <= 0:
                errors.append(f"execution.top_k values must be positive: {kv}")
            top_k_list.append(kv)

        return ExecutionExpectation(
            evaluate_classification=bool(raw.get("evaluate_classification", True)),
            evaluate_retrieval=bool(raw.get("evaluate_retrieval", True)),
            evaluate_patch=bool(raw.get("evaluate_patch", False)),
            top_k=tuple(top_k_list),
            timeout_seconds=timeout,
        )

    def _parse_metadata(
        self, raw: dict, schema_version: str, errors: list[str]
    ) -> CaseMetadata:
        source = str(raw.get("source", "synthetic"))
        if source not in VALID_SOURCES:
            errors.append(f"metadata.source invalid: {source!r}")

        return CaseMetadata(
            source=source,
            reviewed=bool(raw.get("reviewed", False)),
            schema_version=schema_version,
        )

    # ------------------------------------------------------------------
    # 경로 존재 검사 (check_paths=True 일 때만)
    # ------------------------------------------------------------------

    def _validate_paths(self, case: EvaluationCase, errors: list[str]) -> None:
        assert self._root_dir is not None

        repo_dir = self._root_dir / case.repository.path
        if not repo_dir.exists():
            errors.append(
                f"repository.path does not exist: {case.repository.path!r}"
            )
            return  # 이하 파일 검사는 repo_dir 존재를 전제로 한다

        retrieval = case.expected.retrieval
        if retrieval:
            for fpath in retrieval.relevant_files:
                full = repo_dir / fpath
                if not full.exists():
                    errors.append(
                        f"relevant_file does not exist: {fpath!r} "
                        f"(in {case.repository.path})"
                    )

        patch = case.expected.patch
        if patch:
            for rep in patch.expected_replacements:
                full = repo_dir / rep.path
                if not full.exists():
                    errors.append(
                        f"patch replacement path does not exist: {rep.path!r} "
                        f"(in {case.repository.path})"
                    )
