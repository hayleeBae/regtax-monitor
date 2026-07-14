"""V2 실행(run) 계약 — RunContext 와 시간·ID 생성 유틸리티."""

from app.domain.runs.context import RunContext, new_run_id, utc_now

__all__ = ["RunContext", "new_run_id", "utc_now"]
