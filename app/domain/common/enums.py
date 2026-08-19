"""V2 공통 enum 계약.

이름과 값은 docs/architecture/ARCHITECTURE_V2.md 및
docs/specifications/CHANGE_CLASSIFICATION_SPEC.md 에 정의된 것과 일치해야 한다.

모든 enum 은 `str, Enum` 파생 — JSON 직렬화 시 값 문자열이 그대로 나온다
(app.domain.common.serialization 참고). 기존 코드(app/db/models.py 등)는
평문 문자열 컬럼을 쓰므로, 이 enum 값들은 그 문자열 규약과 충돌하지 않도록
새 V2 어휘로만 정의한다. 기존 컬럼 스키마는 변경하지 않는다.
"""

from __future__ import annotations

from enum import Enum


class RunType(str, Enum):
    """실행 종류 — 운영 파이프라인과 평가/재현 실행을 분리한다.

    ARCHITECTURE_V2 §5.9 의 run metadata(`run_type`)와 §5.5 평가 모드 분리,
    §5.7 historical replay 를 근거로 한다.
    """

    PRODUCTION = "production"
    EVALUATION = "evaluation"
    REPLAY = "replay"


class RunStatus(str, Enum):
    """실행 상태 — execution_runs.status 및 RunContext.status 에 사용."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ChangeType(str, Enum):
    """법령 변경 유형.

    값은 ARCHITECTURE_V2 §5.2 / CHANGE_CLASSIFICATION_SPEC §2 와 정확히 일치한다.
    (기존 LawChange.change_type 의 rate/limit/date/formula/logic 자유 문자열과는
     별개의 V2 분류 어휘 — 기존 컬럼은 건드리지 않는다.)
    """

    VALUE_CHANGE = "value_change"
    RATE_CHANGE = "rate_change"
    DATE_CHANGE = "date_change"
    CONDITION_CHANGE = "condition_change"
    TABLE_CHANGE = "table_change"
    NEW_FIELD = "new_field"
    STRUCTURAL_CHANGE = "structural_change"
    NO_CODE_IMPACT = "no_code_impact"
    UNKNOWN = "unknown"


class AutomationDecision(str, Enum):
    """자동화 정책 결정 — ARCHITECTURE_V2 §5.4 / CLASSIFICATION_SPEC §9.

    DB_UPDATE_GUIDANCE: DbDataRegistry 정확 매칭 시 라우팅되는 결정값(ADR-016,
    DB_DATA_ROUTING_SPEC §5) — ANALYSIS_ONLY(미구현/영향 없음)와 구분되는
    "DB에서 갱신하라" 안내.
    """

    DRAFT_ALLOWED = "draft_allowed"
    ANALYSIS_ONLY = "analysis_only"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    DB_UPDATE_GUIDANCE = "db_update_guidance"


class RetrievalSource(str, Enum):
    """검색 후보의 출처 — ARCHITECTURE_V2 §5.3 검색 소스 목록."""

    VERIFIED_MAPPING = "verified_mapping"
    RAG = "rag"
    TERM_DICTIONARY = "term_dictionary"
    CONSTANT_MATCH = "constant_match"
    CODE_GRAPH = "code_graph"
    HISTORICAL_COMMIT = "historical_commit"
