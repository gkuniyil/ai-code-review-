"""
tests for the MCP server wrapper. the MCP SDK lives in requirements-mcp.txt (its own
environment), so these are skipped where it isn't installed.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("mcp")

from backend import mcp_tools  # noqa: E402

EXPECTED_TOOLS = {"list_reviews", "get_review", "list_findings", "get_review_stats", "find_similar_past_issues"}


@pytest.mark.asyncio
async def test_server_exposes_expected_tools():
    tools = await mcp_tools.server.list_tools()

    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.description for t in tools)    # the assistant picks tools by their descriptions


@pytest.mark.asyncio
async def test_every_tool_is_marked_read_only():
    tools = await mcp_tools.server.list_tools()

    for tool in tools:
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


@pytest.mark.asyncio
async def test_tool_call_passes_arguments_through_to_query():
    fake = AsyncMock(return_value=[{"comment": "SQL injection"}])
    with patch.object(mcp_tools.review_queries, "list_findings", fake), \
         patch.object(mcp_tools, "AsyncSessionLocal") as session_factory:
        session_factory.return_value.__aenter__ = AsyncMock(return_value="db")
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await mcp_tools.server.call_tool("list_findings", {"severity": "high", "limit": 5})

    fake.assert_awaited_once_with("db", repo=None, severity="high", agent_type=None, limit=5)
    assert not result.is_error


@pytest.mark.asyncio
async def test_get_review_with_unknown_id_returns_a_helpful_error():
    with patch.object(mcp_tools.review_queries, "get_review_run", AsyncMock(return_value=None)), \
         patch.object(mcp_tools, "AsyncSessionLocal") as session_factory:
        session_factory.return_value.__aenter__ = AsyncMock(return_value="db")
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(mcp_tools.ToolError, match="no review run with id 999"):
            await mcp_tools.server.call_tool("get_review", {"pr_id": 999})
