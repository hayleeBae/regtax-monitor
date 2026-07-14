"""RunContext — 모든 주요 실행에 부여되는 재현 컨텍스트.

ARCHITECTURE_V2 §5.9 의 run metadata 를 순수 Python 값 객체로 표현한다.
DB 모델(#0013 execution_runs)이 아니라, 그 위/아래 계층이 공유하는 계약이다.

불변(frozen)으로 두고 상태 전이는 새 인스턴스를 반환한다(start/complete/fail).
시간·ID 생성 유틸리티(utc_now, new_run_id)를 함께 제공한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from app.domain.common.enums import RunStatus, RunType
from app.domain.common.serialization import to_jsonable

_RUN_ID_PREFIX = "run_"


def utc_now() -> datetime:
    """tz-aware UTC 현재 시각.

    naive datetime 을 반환하지 않는다(직렬화 시 UTC 명시를 보장하기 위함).
    """
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    """전역적으로 유일한 run_id 를 생성한다.

    형식: `run_<uuid4 hex 32자>`. 정렬 가능성이 필요하면 후속 issue 에서
    생성기를 교체할 수 있으나(설계 근거 참고), 유일성만이 이 계약의 보장이다.
    """
    return f"{_RUN_ID_PREFIX}{uuid.uuid4().hex}"


@dataclass(frozen=True)
class RunContext:
    """실행 1건의 재현 메타데이터.

    prompt_versions 는 생성 시 읽기 전용 매핑으로 고정되어, 인스턴스가
    불변임을 보장한다. 상태 전이는 with_status / start / complete / fail 이
    새 인스턴스를 반환한다.
    """

    run_id: str
    run_type: RunType
    status: RunStatus = RunStatus.CREATED
    law_change_id: int | None = None
    repository_commit: str | None = None
    source_hash: str | None = None
    settings_hash: str | None = None
    embedding_model: str | None = None
    llm_backend: str | None = None
    llm_model: str | None = None
    prompt_versions: Mapping[str, str] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not str(self.run_id).strip():
            raise ValueError("RunContext.run_id must be non-empty")
        if not isinstance(self.run_type, RunType):
            raise TypeError("RunContext.run_type must be a RunType")
        if not isinstance(self.status, RunStatus):
            raise TypeError("RunContext.status must be a RunStatus")
        # 불변성 보장: 전달된 매핑을 복사해 읽기 전용으로 고정한다.
        object.__setattr__(
            self,
            "prompt_versions",
            MappingProxyType(dict(self.prompt_versions)),
        )

    # --- 상태 전이 (새 인스턴스 반환) --------------------------------------

    def with_status(self, status: RunStatus, **changes) -> "RunContext":
        return replace(self, status=status, **changes)

    def start(self, now: datetime | None = None) -> "RunContext":
        return self.with_status(RunStatus.RUNNING, started_at=now or utc_now())

    def complete(self, now: datetime | None = None) -> "RunContext":
        return self.with_status(RunStatus.COMPLETED, completed_at=now or utc_now())

    def fail(self, now: datetime | None = None) -> "RunContext":
        return self.with_status(RunStatus.FAILED, completed_at=now or utc_now())

    # --- 직렬화 ------------------------------------------------------------

    def to_dict(self) -> dict:
        return to_jsonable(self)
