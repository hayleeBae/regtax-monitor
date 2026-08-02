"""과거 개정 replay fixture 스키마 — HISTORICAL_REPLAY_SPEC.md §3·§8, ADR-010.

`EvaluationCase`(#0005)와 분리된 별도 스키마다 — 스펙의 YAML 모양
(`source_type`/`path_env`/`scope`/`privacy_mode`)이 애초에 다르고, 억지로 합치면
두 용도가 서로의 검증 규칙을 오염시킨다(ADR-010). `ExpectedReplacement`·`LawInput`
만 `app.evaluation.case` 에서 재사용한다.

이 모듈은 **선언만 담는 계약**이다(ARCHITECTURE.md 레이어 규칙) — 표준 라이브러리
외에는 아무것도 import 하지 않으며 YAML 파싱·파일 읽기·git 실행·경로 존재 검사를
하지 않는다. 입력 검증은 로더(#0017 Step 1), 실행과 저장 억제는 runner(#0018)의
책임이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.evaluation.case import ExpectedReplacement, LawInput

REPLAY_SCHEMA_VERSION = "1"
"""replay fixture 스키마 버전 — 스펙 §3 `schema_version`."""


# ---------------------------------------------------------------------------
# Privacy 어휘 (스펙 §8)
# ---------------------------------------------------------------------------


class PrivacyMode(str, Enum):
    """replay 실행 산출물의 저장 수준 — 스펙 §8."""

    FULL = "full"
    REDACTED = "redacted"
    METADATA_ONLY = "metadata_only"


class ArtifactKind(str, Enum):
    """replay runner 가 저장할 수 있는 산출물 종류."""

    DIFF_BODY = "diff_body"  # diff 의 +/- 실제 코드 라인
    DIFF_STRUCTURE = "diff_structure"  # 파일 목록·hunk 헤더·변경 줄 수
    FILE_PATH = "file_path"  # 실제 상대 경로
    CODE_SNIPPET = "code_snippet"  # 코드 본문 발췌
    GOLDEN_OUTPUT = "golden_output"  # 골든 테스트 표준출력
    METRIC = "metric"  # 수치 지표
    HASH = "hash"
    COUNT = "count"


_SAFE_ALWAYS: frozenset[ArtifactKind] = frozenset(
    {ArtifactKind.METRIC, ArtifactKind.HASH, ArtifactKind.COUNT}
)
"""어느 모드에서도 코드를 담지 않는 산출물 — 수치·해시·개수."""

_ALLOWED_ARTIFACTS: dict[PrivacyMode, frozenset[ArtifactKind]] = {
    PrivacyMode.FULL: frozenset(ArtifactKind),
    PrivacyMode.REDACTED: _SAFE_ALWAYS
    | frozenset({ArtifactKind.DIFF_STRUCTURE, ArtifactKind.FILE_PATH}),
    PrivacyMode.METADATA_ONLY: _SAFE_ALWAYS,
}
"""모드별 저장 허용 집합 — 스펙 §8.

**화이트리스트**다. "위험한 것을 찾아 지우는" 마스킹(블랙리스트)이 아니라 "안전한
것만 남기는" 방식이라, 새 artifact 종류를 추가할 때 이 표에 넣는 것을 잊으면
저장이 막힐 뿐 유출되지 않는다 — 누락으로 인한 코드 반출이 구조적으로 발생하지
않는다(CLAUDE.md 코드 반출 금지).

`REDACTED` 가 `DIFF_STRUCTURE`·`FILE_PATH` 를 허용하는 이유: `METADATA_ONLY` 는
"뭔가 틀렸다"만 알려주고 "어디서 틀렸는지"를 답하지 못해 회사 환경에서 디버깅이
불가능하다. 구조(파일·hunk 헤더·변경 줄 수)까지 있으면 코드 본문 없이도 어긋난
지점을 특정할 수 있다. 반대로 `DIFF_BODY`·`CODE_SNIPPET`·`GOLDEN_OUTPUT` 은 코드
본문(골든 출력은 스택트레이스에 코드가 나온다)이므로 `FULL` 에서만 허용한다.
"""


def allowed_artifacts(mode: PrivacyMode) -> frozenset[ArtifactKind]:
    """해당 privacy 모드에서 저장이 허용된 artifact 종류 집합 — 스펙 §8.

    실제 저장 억제는 #0018 runner 의 책임이고, 이 함수는 그 판단 기준만 제공한다.
    """
    return _ALLOWED_ARTIFACTS[PrivacyMode(mode)]


# ---------------------------------------------------------------------------
# Fixture 계약 (스펙 §3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayRepository:
    """replay 대상 repo 와 base/answer commit — 스펙 §3.

    `path` 는 프로젝트 상대 경로(mock 전용), `path_env` 는 절대경로를 담은 환경변수
    이름(실데이터 전용)이다. 회사 repo 절대경로가 YAML 에 남지 않게 하기 위한 분리다
    (ADR-010). **둘 중 정확히 하나만 허용하는 검증은 로더의 책임**이다.
    """

    source_type: str  # 현재 "local_git" 만 유효
    base_commit: str
    answer_commit: str
    path: Optional[str] = None  # 프로젝트 상대 경로 (mock 전용)
    path_env: Optional[str] = None  # 절대경로를 담은 환경변수 이름 (실데이터 전용)


@dataclass(frozen=True)
class ReplayScope:
    """사람이 지정한 relevant scope — 스펙 §2 "answer commit 전체를 정답으로 보지 않음"."""

    relevant_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...] = ()
    expected_replacements: tuple[ExpectedReplacement, ...] = ()


@dataclass(frozen=True)
class ReplayExecution:
    """실행 파라미터와 저장 정책 — 스펙 §3·§8.

    `privacy_mode` 기본값은 `METADATA_ONLY` 다 — 스펙 §8 이 "실제 회사 사례는
    metadata_only 를 기본으로 한다"고 정했고, 기본값이 느슨하면 fixture 가 모드를
    생략했을 때 실수로 코드 본문이 디스크에 남는다.
    """

    privacy_mode: PrivacyMode = PrivacyMode.METADATA_ONLY
    golden_command: Optional[str] = None
    timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class ReplayFixture:
    """과거 개정 replay 케이스 한 건의 완전한 표현 — 스펙 §3.

    `__post_init__` 에는 자기완결적 불변식만 둔다. `path` XOR `path_env` 같은 입력
    검증은 로더가 다른 오류들과 함께 모아 보고해야 하므로 여기서 먼저 예외를 던지지
    않는다(ADR-010).
    """

    case_id: str
    law: LawInput
    repository: ReplayRepository
    scope: ReplayScope
    execution: ReplayExecution
    reviewed: bool = False
    schema_version: str = REPLAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
