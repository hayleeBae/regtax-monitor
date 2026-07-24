"""공식 MCP Python SDK 기반 stdio 읽기 전용 서버."""

from __future__ import annotations

from app.db.database import SessionLocal, init_db
from app.mcp.service import ReadOnlyMcpService
from config import settings


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    service = ReadOnlyMcpService(
        SessionLocal,
        artifact_root=settings.audit_artifact_dir,
        expose_patch_drafts=settings.mcp_expose_patch_drafts,
    )
    server = FastMCP(
        "regtax-monitor",
        instructions=(
            "법령 변경, 실행 감사 기록, 산출물을 조회하는 읽기 전용 서버입니다. "
            "승인 또는 patch 적용 기능은 제공하지 않습니다."
        ),
    )
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    server.tool(annotations=read_only)(service.list_changes)
    server.tool(annotations=read_only)(service.get_change)
    server.tool(annotations=read_only)(service.get_execution_run)
    server.tool(annotations=read_only)(service.get_audit_events)
    server.tool(annotations=read_only)(service.get_run_artifacts)
    server.tool(annotations=read_only)(service.get_patch_draft)
    return server


def main() -> None:
    init_db()
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
