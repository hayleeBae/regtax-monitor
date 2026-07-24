"""읽기 전용 MCP service의 노출 범위 테스트."""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import LawChange, PatchProposal
from app.mcp.service import READ_ONLY_TOOL_NAMES, ReadOnlyMcpService
from app.mcp.server import create_mcp_server


def _service(*, expose_patch_drafts: bool = False):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        change = LawChange(
            law_id="LAW-1",
            law_name="소득세법",
            article_no="제1조",
            before_text="이전",
            after_text="이후",
            status="reviewing",
        )
        session.add(change)
        session.commit()
        session.refresh(change)
        session.add(
            PatchProposal(
                law_change_id=change.id,
                diff="--- a/A.java\n+++ b/A.java",
                approval_status="draft",
            )
        )
        session.commit()
    return ReadOnlyMcpService(factory, expose_patch_drafts=expose_patch_drafts)


def test_mcp_tool_surface_has_no_mutating_operations() -> None:
    assert READ_ONLY_TOOL_NAMES == {
        "list_changes",
        "get_change",
        "get_execution_run",
        "get_audit_events",
        "get_run_artifacts",
        "get_patch_draft",
    }
    assert not any(
        word in name
        for name in READ_ONLY_TOOL_NAMES
        for word in ("apply", "approve", "reject", "update", "delete", "write")
    )


def test_fastmcp_server_registers_only_read_only_tools() -> None:
    tools = asyncio.run(create_mcp_server().list_tools())

    assert {tool.name for tool in tools} == READ_ONLY_TOOL_NAMES
    assert all(tool.annotations.readOnlyHint is True for tool in tools)
    assert all(tool.annotations.destructiveHint is False for tool in tools)


def test_change_queries_return_data_without_absolute_repository_path() -> None:
    service = _service()
    changes = service.list_changes(limit=10)
    detail = service.get_change(changes[0]["id"])

    assert changes[0]["law_name"] == "소득세법"
    assert detail["before_text"] == "이전"
    assert "repo_root" not in str(detail)


def test_patch_content_is_hidden_by_default_and_opt_in() -> None:
    hidden = _service().get_patch_draft(1)
    visible = _service(expose_patch_drafts=True).get_patch_draft(1)

    assert hidden["content_exposed"] is False
    assert "diff" not in hidden
    assert visible["content_exposed"] is True
    assert visible["diff"].startswith("---")
