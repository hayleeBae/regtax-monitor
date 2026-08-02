"""ReplayFixtureLoader — replay fixture YAML 로더 및 유효성 검사 (Issue #0017).

HISTORICAL_REPLAY_SPEC.md §3(fixture 포맷)·§5(git allowlist)와 ADR-010 을 구현한다.

fixture YAML 은 이 이슈의 **유일한 외부 입력 지점**이고, 여기서 읽은 값이 #0018
runner 에서 git 인자와 골든 명령으로 흘러간다. 그래서 이 로더는 스키마 검사만이
아니라 **주입 차단 지점**이다 — repo 위치(path XOR path_env), git revision 문자
집합, golden_command 실행파일 allowlist, scope 경로 traversal 네 가지를 입구에서
막는다.

`app/evaluation/loader.py`(DatasetLoader)와 같은 방식으로 **첫 오류에서 멈추지 않고**
케이스별 오류를 모아 `DatasetValidationError.details` 로 한 번에 보고한다. fixture
한 벌을 고치자고 여러 번 왕복하지 않기 위해서다.

이 모듈은 YAML 을 `yaml.safe_load` 로만 읽고, git·subprocess 를 호출하지 않으며,
`path_env` 가 가리키는 값을 **읽지 않는다**(아래 `_parse_repository` 주석 참조).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from app.evaluation.case import ExpectedReplacement, LawInput
from app.evaluation.errors import DatasetValidationError
from app.evaluation.loader import MAX_TIMEOUT, MIN_TIMEOUT, SUPPORTED_MATCH_MODES
from app.evaluation.replay.fixture import (
    REPLAY_SCHEMA_VERSION,
    PrivacyMode,
    ReplayExecution,
    ReplayFixture,
    ReplayRepository,
    ReplayScope,
)

# ---------------------------------------------------------------------------
# 허용 값 상수
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({REPLAY_SCHEMA_VERSION})
SUPPORTED_SOURCE_TYPES: frozenset[str] = frozenset({"local_git"})

DEFAULT_TIMEOUT_SECONDS: int = 1800

ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
"""`path_env` 에 담을 수 있는 이름 형식 — 대문자로 시작하는 관례적 변수명만."""

REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
"""git revision 허용 문자 집합 — SHA·태그명(`case1/base`)까지만 통과한다(ADR-010)."""

GOLDEN_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {"mvn", "gradle", "./gradlew", "pytest"}
)
"""`golden_command` 첫 토큰(실행파일)로 허용하는 값.

`config.golden_test_cmd` 는 운영자가 `.env` 에 직접 넣는 값이지만 fixture YAML 은
**파일로 주고받을 수 있어 신뢰 수준이 다르다** — 같은 명령이라도 출처가 다르므로
입구에서 실행파일을 대조한다(ADR-010). 이 로더는 명령을 실행하지 않고 검증만
한다(실행은 #0018, 그때도 shell 없이 인자 배열로 넘긴다).

**범용 셸(`bash`/`sh`/`zsh`)은 넣지 않는다.** 허용된 빌드·테스트 도구는 *replay 대상
repo 안의* 빌드 스크립트를 실행하며, 그 repo를 신뢰하는 것은 replay 의 전제다. 반면
`bash -c "<문자열>"` 은 *fixture YAML 에 적힌 문자열* 을 실행한다 — 신뢰 경계가 다른
별개 채널이고, allowlist 를 둔 이유 자체를 무력화한다(`bash -c "curl … | sh"` 가
통과해 버린다). 골든 검증이 셸 스크립트여야 한다면 그 스크립트를 호출하는 도구를
allowlist 에 명시적으로 추가하라.
"""


class ReplayFixtureLoader:
    """replay fixture YAML 을 로드하고 유효성을 검사한다.

    `check_paths=True` 이고 `root_dir` 이 주어지면 `repository.path` 가 가리키는
    디렉토리 존재를 확인한다. `path_env` 케이스는 이름만 알 뿐 실제 경로를 모르므로
    (로더가 값을 읽지 않는다) 경로 검사를 건너뛴다.
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

    def load_yaml(self, path: Union[str, Path]) -> list[ReplayFixture]:
        """YAML 파일에서 replay fixture 목록을 로드한다.

        지원 형식은 `DatasetLoader.load_yaml` 과 같다 — `cases:` 키를 가진 dict,
        케이스 dict 목록, 케이스 dict 하나.
        """
        path = Path(path)
        if not path.exists():
            raise DatasetValidationError(f"Replay fixture file not found: {path}")
        try:
            # safe_load 만 쓴다 — yaml.load 는 임의 객체 생성이 가능해 fixture 파일이
            # 코드 실행 경로가 된다.
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

        return self._load_and_validate(raw_cases)

    # ------------------------------------------------------------------
    # 내부 로직
    # ------------------------------------------------------------------

    def _load_and_validate(self, raw_cases: Any) -> list[ReplayFixture]:
        if not isinstance(raw_cases, list):
            raise DatasetValidationError("Expected a list of replay fixture dicts")

        errors: list[str] = []
        seen_ids: set[str] = set()
        fixtures: list[ReplayFixture] = []

        for i, raw in enumerate(raw_cases):
            if not isinstance(raw, dict):
                errors.append(f"[index {i}] expected dict, got {type(raw).__name__}")
                continue

            case_id = str(raw.get("case_id", f"<index {i}>"))
            case_errors: list[str] = []

            sv = str(raw.get("schema_version", REPLAY_SCHEMA_VERSION))
            if sv not in SUPPORTED_SCHEMA_VERSIONS:
                case_errors.append(f"unsupported schema_version: {sv!r}")

            if case_id in seen_ids:
                case_errors.append(f"duplicate case_id: {case_id!r}")
            seen_ids.add(case_id)

            try:
                fixture = self._parse_fixture(raw, sv, case_errors)
            except Exception as exc:  # dataclass 불변식 등
                case_errors.append(f"parse error: {exc}")
                errors.extend(f"[{case_id}] {e}" for e in case_errors)
                continue

            if self._check_paths and self._root_dir is not None:
                self._validate_paths(fixture, case_errors)

            if case_errors:
                errors.extend(f"[{case_id}] {e}" for e in case_errors)
            else:
                fixtures.append(fixture)

        if errors:
            raise DatasetValidationError(
                f"Replay fixture validation failed ({len(errors)} error(s))",
                details=errors,
            )
        return fixtures

    # ------------------------------------------------------------------
    # 케이스 파싱
    # ------------------------------------------------------------------

    def _parse_fixture(
        self, raw: dict, schema_version: str, errors: list[str]
    ) -> ReplayFixture:
        case_id = str(raw.get("case_id", "")).strip()
        if not case_id:
            errors.append("case_id is required")

        law = self._parse_law(raw.get("law") or {}, errors)
        repository = self._parse_repository(raw.get("repository") or {}, errors)
        scope = self._parse_scope(raw.get("scope") or {}, errors)
        execution = self._parse_execution(raw.get("execution") or {}, errors)

        metadata = raw.get("metadata") or {}
        reviewed = bool(metadata.get("reviewed", raw.get("reviewed", False)))

        return ReplayFixture(
            # case_id 가 비면 dataclass 불변식에 걸린다. 오류는 이미 쌓았고 이 객체는
            # 반환되지 않으므로, 나머지 오류까지 함께 보고하려고 자리표시자로 만든다.
            case_id=case_id or "<missing case_id>",
            law=law,
            repository=repository,
            scope=scope,
            execution=execution,
            reviewed=reviewed,
            schema_version=schema_version,
        )

    def _parse_law(self, raw: dict, errors: list[str]) -> LawInput:
        law_name = str(raw.get("law_name", ""))
        if not law_name:
            errors.append("law.law_name is required")

        return LawInput(
            law_name=law_name,
            tier=str(raw.get("tier", "law")),
            before_text=str(raw.get("before_text", "")),
            after_text=str(raw.get("after_text", "")),
            article=raw.get("article"),
            effective_date=raw.get("effective_date"),
        )

    def _parse_repository(self, raw: dict, errors: list[str]) -> ReplayRepository:
        source_type = str(raw.get("source_type", "local_git"))
        if source_type not in SUPPORTED_SOURCE_TYPES:
            errors.append(f"repository.source_type invalid: {source_type!r}")

        path = _optional_str(raw.get("path"))
        path_env = _optional_str(raw.get("path_env"))

        # path XOR path_env — 회사 repo 절대경로가 YAML 에 남으면 fixture 파일 자체가
        # 반출 위험물이 되므로, 실데이터는 경로 대신 변수 이름만 적는다(ADR-010).
        if path and path_env:
            errors.append("repository: path and path_env are mutually exclusive")
        elif not path and not path_env:
            errors.append("repository: exactly one of path or path_env is required")

        if path:
            _check_relative_path(path, "repository.path", errors)

        if path_env and not ENV_NAME_PATTERN.match(path_env):
            errors.append(
                f"repository.path_env must match [A-Z][A-Z0-9_]*: {path_env!r}"
            )

        # 로더는 path_env 가 가리키는 값을 **읽지 않는다**. 여기서 해석하면 로드 시점에
        # 회사 절대경로가 메모리와 오류 메시지에 실린다 — 해석은 #0018 runner 의 몫이다.

        base_commit = str(raw.get("base_commit", ""))
        answer_commit = str(raw.get("answer_commit", ""))
        _check_revision(base_commit, "repository.base_commit", errors)
        _check_revision(answer_commit, "repository.answer_commit", errors)

        return ReplayRepository(
            source_type=source_type,
            base_commit=base_commit,
            answer_commit=answer_commit,
            path=path,
            path_env=path_env,
        )

    def _parse_scope(self, raw: dict, errors: list[str]) -> ReplayScope:
        relevant = tuple(str(p) for p in raw.get("relevant_paths") or ())
        excluded = tuple(str(p) for p in raw.get("excluded_paths") or ())

        for p in relevant:
            _check_relative_path(p, "scope.relevant_paths", errors)
        for p in excluded:
            _check_relative_path(p, "scope.excluded_paths", errors)

        overlap = set(relevant) & set(excluded)
        if overlap:
            errors.append(
                "paths appear in both relevant_paths and excluded_paths: "
                f"{sorted(overlap)}"
            )

        replacements: list[ExpectedReplacement] = []
        for rep in raw.get("expected_replacements") or ():
            if not isinstance(rep, dict):
                errors.append(
                    f"scope.expected_replacements entry must be a mapping: {rep!r}"
                )
                continue
            rep_path = str(rep.get("path", ""))
            _check_relative_path(
                rep_path, "scope.expected_replacements[].path", errors
            )
            match_mode = str(rep.get("match_mode", "exact"))
            if match_mode not in SUPPORTED_MATCH_MODES:
                errors.append(
                    f"scope.expected_replacements[].match_mode invalid: {match_mode!r}"
                )
            replacements.append(
                ExpectedReplacement(
                    path=rep_path,
                    before=str(rep.get("before", "")),
                    after=str(rep.get("after", "")),
                    match_mode=match_mode,
                )
            )

        return ReplayScope(
            relevant_paths=relevant,
            excluded_paths=excluded,
            expected_replacements=tuple(replacements),
        )

    def _parse_execution(self, raw: dict, errors: list[str]) -> ReplayExecution:
        privacy_raw = raw.get("privacy_mode", PrivacyMode.METADATA_ONLY.value)
        try:
            privacy_mode = PrivacyMode(str(privacy_raw))
        except ValueError:
            errors.append(f"execution.privacy_mode invalid: {privacy_raw!r}")
            # 알 수 없는 모드는 가장 엄격한 쪽으로 두고 계속 검사한다.
            privacy_mode = PrivacyMode.METADATA_ONLY

        try:
            timeout = int(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            errors.append(
                f"execution.timeout_seconds must be an integer: "
                f"{raw.get('timeout_seconds')!r}"
            )
            timeout = DEFAULT_TIMEOUT_SECONDS
        if not (MIN_TIMEOUT <= timeout <= MAX_TIMEOUT):
            errors.append(
                f"execution.timeout_seconds out of range "
                f"[{MIN_TIMEOUT}, {MAX_TIMEOUT}]: {timeout}"
            )
            # dataclass 불변식(양수)에 걸려 나머지 오류가 묻히지 않도록 기본값으로 만든다.
            timeout = DEFAULT_TIMEOUT_SECONDS

        golden_command = _optional_str(raw.get("golden_command"))
        if golden_command:
            _check_golden_command(golden_command, errors)

        return ReplayExecution(
            privacy_mode=privacy_mode,
            golden_command=golden_command,
            timeout_seconds=timeout,
        )

    # ------------------------------------------------------------------
    # 경로 존재 검사 (check_paths=True 일 때만)
    # ------------------------------------------------------------------

    def _validate_paths(self, fixture: ReplayFixture, errors: list[str]) -> None:
        assert self._root_dir is not None

        repo_path = fixture.repository.path
        if not repo_path:
            # path_env 케이스 — 값을 읽지 않으므로 존재를 확인할 수 없다.
            return
        if not (self._root_dir / repo_path).exists():
            errors.append(f"repository.path does not exist: {repo_path!r}")


# ---------------------------------------------------------------------------
# 검증 헬퍼
# ---------------------------------------------------------------------------


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _check_relative_path(value: str, label: str, errors: list[str]) -> None:
    """프로젝트 상대 경로만 허용한다 — 절대경로와 `..` 구성요소를 거부."""
    if not value:
        errors.append(f"{label} must not be empty")
        return
    if value.startswith("/") or Path(value).is_absolute():
        errors.append(f"{label} must be a relative path: {value!r}")
    if ".." in Path(value).parts:
        errors.append(f"{label} must not contain '..': {value!r}")


def _check_revision(value: str, label: str, errors: list[str]) -> None:
    """git revision 문자 제한 — `-` 로 시작하는 값을 특히 막는다.

    `--upload-pack=...` 같은 값이 git 인자로 해석되면 임의 명령 실행이 된다
    (#0018 이 이 값을 git 에 넘긴다). `^`·`~`·`:`·공백은 문자 집합에서 걸러지고,
    `..` 는 revision range 로 해석되므로 별도로 거부한다(ADR-010).
    """
    if not value:
        errors.append(f"{label} is required")
        return
    if value.startswith("-"):
        errors.append(f"{label} must not start with '-': {value!r}")
        return
    if ".." in value:
        errors.append(f"{label} must not contain '..': {value!r}")
    if not REVISION_PATTERN.match(value):
        errors.append(
            f"{label} contains characters outside [A-Za-z0-9._/-]: {value!r}"
        )


def _check_golden_command(command: str, errors: list[str]) -> None:
    """첫 토큰(실행파일)만 allowlist 와 대조한다.

    부분일치가 아니라 토큰화 후 첫 토큰을 보는 이유: `echo mvn; rm -rf /` 처럼
    allowlist 단어를 포함하기만 한 값이 통과하기 때문이다.
    """
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        errors.append(f"execution.golden_command is not parseable: {exc}")
        return
    if not tokens:
        errors.append(f"execution.golden_command is empty: {command!r}")
        return

    executable = tokens[0]
    if executable in GOLDEN_COMMAND_ALLOWLIST:
        return  # './gradlew' 는 allowlist 에 명시된 예외다
    if "/" in executable:
        errors.append(
            f"execution.golden_command executable must not be a path: {executable!r}"
        )
        return
    errors.append(
        f"execution.golden_command executable not allowed: {executable!r} "
        f"(allowed: {sorted(GOLDEN_COMMAND_ALLOWLIST)})"
    )
