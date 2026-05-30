"""Integration tests: proxy wraps a test MCP server and meters calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agentmeter.db import MeterDB

pytestmark = pytest.mark.anyio


async def _connect_to_proxy(
    test_server_path: str,
    db_path: Path,
) -> tuple[StdioServerParameters, dict[str, str]]:
    """Build connection params for the proxy wrapping the test server."""
    env = {**os.environ, "AGENTMETER_DB": str(db_path)}
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "agentmeter.cli", "wrap",
            "--name", "test",
            sys.executable, test_server_path,
        ],
        env=env,
    )
    return params, env


async def test_proxy_forwards_tool_calls(test_server_path: str, tmp_path: Path) -> None:
    """Proxy forwards tool calls and records them in the DB."""
    db_path = tmp_path / "test.db"
    params, _ = await _connect_to_proxy(test_server_path, db_path)

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # List tools
        tools = await session.list_tools()
        tool_names = [t.name for t in tools.tools]
        assert "add" in tool_names
        assert "echo" in tool_names
        assert "fail" in tool_names

        # Call add
        result = await session.call_tool("add", {"a": 3, "b": 4})
        assert result.content[0].text == "7"

        # Call echo
        result = await session.call_tool("echo", {"message": "hello"})
        assert result.content[0].text == "Echo: hello"

        # Call add again
        result = await session.call_tool("add", {"a": 100, "b": 200})
        assert result.content[0].text == "300"

    # Verify metering DB
    db = MeterDB(db_path)
    assert db.get_total_calls() >= 3

    calls = db.get_recent_calls()
    tool_names_recorded = [c.tool_name for c in calls]
    assert "add" in tool_names_recorded
    assert "echo" in tool_names_recorded

    # All successful calls should not be errors
    for c in calls:
        assert c.is_error is False
        assert c.elapsed_ms >= 0
        assert c.result_size > 0

    db.close()


async def test_proxy_records_failed_tool_calls(
    test_server_path: str, tmp_path: Path,
) -> None:
    """Failed tool calls are recorded with is_error=True."""
    db_path = tmp_path / "test.db"
    params, _ = await _connect_to_proxy(test_server_path, db_path)

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # Call the tool that raises ToolError — SDK sets isError=True
        result = await session.call_tool("fail", {})
        assert result.isError is True

    # Verify the error was recorded
    db = MeterDB(db_path)
    calls = db.get_recent_calls(tool_name="fail")
    assert len(calls) >= 1
    assert calls[0].is_error is True
    assert calls[0].elapsed_ms >= 0

    # Error should show up in stats
    stats = db.get_tool_stats()
    fail_stats = [s for s in stats if s.tool_name == "fail"]
    assert len(fail_stats) == 1
    assert fail_stats[0].error_count == 1

    db.close()


async def test_proxy_session_tracking(test_server_path: str, tmp_path: Path) -> None:
    """Proxy creates and closes sessions correctly."""
    db_path = tmp_path / "test.db"
    params, _ = await _connect_to_proxy(test_server_path, db_path)

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await session.call_tool("add", {"a": 1, "b": 2})

    db = MeterDB(db_path)
    sessions = db.get_session_stats()
    assert len(sessions) == 1
    assert sessions[0].server_name == "test"
    assert sessions[0].total_calls >= 1
    db.close()
