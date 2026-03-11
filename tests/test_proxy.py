"""Integration test: proxy wraps a test MCP server and meters calls."""

import sys
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agentmeter.db import MeterDB

TEST_DB = Path("/tmp/agentmeter_test.db")
TEST_SERVER = str(Path(__file__).parent / "test_server.py")


async def run_test() -> None:
    # Clean up previous test DB
    if TEST_DB.exists():
        TEST_DB.unlink()

    # Connect to the proxy, which wraps the test server
    proxy_command = sys.executable
    proxy_args = [
        "-m", "agentmeter.cli", "wrap",
        "--name", "test",
        sys.executable, TEST_SERVER,
    ]

    # Override DB path via env
    import os
    env = {**os.environ, "AGENTMETER_DB": str(TEST_DB)}

    params = StdioServerParameters(
        command=proxy_command,
        args=proxy_args,
        env=env,
    )

    print("Connecting to proxy...")
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
            await session.initialize()

            # List tools — should show test server's tools
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"Tools: {tool_names}")
            assert "add" in tool_names, f"Expected 'add' in tools, got {tool_names}"
            assert "echo" in tool_names, f"Expected 'echo' in tools, got {tool_names}"

            # Call add
            result = await session.call_tool("add", {"a": 3, "b": 4})
            print(f"add(3, 4) = {result.content[0].text}")

            # Call echo
            result = await session.call_tool("echo", {"message": "hello agentmeter"})
            print(f"echo = {result.content[0].text}")

            # Call add again
            result = await session.call_tool("add", {"a": 100, "b": 200})
            print(f"add(100, 200) = {result.content[0].text}")

    print("\nProxy closed. Checking metering DB...")

    # Verify metering data was recorded
    db = MeterDB(TEST_DB)
    stats = db.get_tool_stats()
    print(f"Recorded tool stats: {[(s.tool_name, s.call_count) for s in stats]}")

    total = db.get_total_calls()
    print(f"Total calls recorded: {total}")

    assert total >= 3, f"Expected at least 3 calls, got {total}"

    calls = db.get_recent_calls()
    for c in calls:
        print(
            f"  {c.tool_name}: {c.elapsed_ms}ms, "
            f"{c.result_size}B, error={c.is_error}"
        )

    db.close()

    # Clean up
    if TEST_DB.exists():
        TEST_DB.unlink()

    print("\nAll tests passed!")


if __name__ == "__main__":
    anyio.run(run_test)
