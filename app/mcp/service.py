"""MCP transport와 분리된 읽기 전용 조회 서비스."""

from __future__ import annotations

from pathlib import Path

from app.audit.artifacts import LocalArtifactStore
from app.audit.replay import InspectionReplay
from app.audit.repository import SqlAlchemyAuditRepository
from app.db.models import LawChange, PatchProposal
from app.domain.common.serialization import to_jsonable

READ_ONLY_TOOL_NAMES = {
    "list_changes",
    "get_change",
    "get_execution_run",
    "get_audit_events",
    "get_run_artifacts",
    "get_patch_draft",
}


class ReadOnlyMcpService:
    def __init__(
        self,
        session_factory,
        *,
        artifact_root: str | Path = "data/audit",
        expose_patch_drafts: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.expose_patch_drafts = expose_patch_drafts

    def list_changes(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        safe_limit = max(1, min(limit, 200))
        with self.session_factory() as session:
            query = session.query(LawChange)
            if status:
                query = query.filter(LawChange.status == status)
            rows = query.order_by(LawChange.id.desc()).limit(safe_limit).all()
            return [
                {
                    "id": row.id,
                    "law_name": row.law_name,
                    "article_no": row.article_no,
                    "domain": row.domain,
                    "change_type": row.change_type,
                    "status": row.status,
                    "effective_date": row.effective_date,
                    "ai_summary": row.ai_summary,
                }
                for row in rows
            ]

    def get_change(self, change_id: int) -> dict:
        with self.session_factory() as session:
            row = session.get(LawChange, change_id)
            if row is None:
                raise ValueError(f"change not found: {change_id}")
            return {
                "id": row.id,
                "law_id": row.law_id,
                "law_name": row.law_name,
                "article_no": row.article_no,
                "domain": row.domain,
                "change_type": row.change_type,
                "status": row.status,
                "before_text": row.before_text,
                "after_text": row.after_text,
                "ai_summary": row.ai_summary,
                "ai_impact": row.ai_impact,
            }

    def get_execution_run(self, run_id: str) -> dict:
        with self.session_factory() as session:
            return to_jsonable(SqlAlchemyAuditRepository(session).get_run(run_id))

    def get_audit_events(self, run_id: str) -> list[dict]:
        with self.session_factory() as session:
            repository = SqlAlchemyAuditRepository(session)
            repository.get_run(run_id)
            return [to_jsonable(event) for event in repository.list_events(run_id)]

    def get_run_artifacts(self, run_id: str) -> dict:
        return InspectionReplay(self.artifact_store).inspect(run_id)

    def get_patch_draft(self, proposal_id: int) -> dict:
        with self.session_factory() as session:
            row = session.get(PatchProposal, proposal_id)
            if row is None:
                raise ValueError(f"proposal not found: {proposal_id}")
            result = {
                "proposal_id": row.id,
                "law_change_id": row.law_change_id,
                "approval_status": row.approval_status,
                "golden_status": row.golden_status,
                "model_used": row.model_used,
                "content_exposed": self.expose_patch_drafts,
            }
            if self.expose_patch_drafts:
                result["diff"] = row.diff
                result["golden_output"] = row.golden_output
            return result

