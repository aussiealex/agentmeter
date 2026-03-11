"""MCP proxy that meters every tool call.

Sits between an MCP client (Claude Code, Cursor) and an MCP server,
forwarding all requests while recording tool call metrics to SQLite.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime
from time import perf_counter

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ReadResourceResult,
    TextContent,
    Tool,
)

from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall


class AgentMeterProxy:
    """MCP proxy that meters tool calls between client and server."""

    def __init__(
        self,
        command: str,
        args: list[str],
        server_name: str = "",
        db: MeterDB | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._args = args
        self._server_name = server_name or self._infer_server_name(command, args)
        self._db = db or MeterDB()
        self._env = env
        self._session_id = uuid.uuid4().hex[:12]
        self._call_count = 0
        self._client_session: ClientSession | None = None
        self._tools: list[Tool] = []

    @staticmethod
    def _infer_server_name(command: str, args: list[str]) -> str:
        """Infer a human-readable server name from the command."""
        parts = [command] + args
        for part in parts:
            if "." in part and not part.startswith("-"):
                # e.g. "mailsift.mcp.server" -> "mailsift"
                return part.split(".")[0]
        return command

    async def run(self) -> None:
        """Start the proxy: connect to child server, serve to parent client."""
        # Record session start
        session = Session(
            id=self._session_id,
            server_name=self._server_name,
            server_command=f"{self._command} {' '.join(self._args)}",
            started_at=datetime.now().isoformat(),
        )
        self._db.create_session(session)

        child_params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._env,
        )

        # Connect to child MCP server
        async with (
            stdio_client(child_params) as (child_read, child_write),
            ClientSession(child_read, child_write) as client_session,
        ):
                self._client_session = client_session

                # Initialize the child server
                await client_session.initialize()

                # Discover tools from child
                tools_result = await client_session.list_tools()
                self._tools = tools_result.tools

                # Create proxy server that re-exports child's tools
                proxy_server = self._build_proxy_server()

                # Serve to parent (Claude Code) via stdio
                async with stdio_server() as (parent_read, parent_write):
                    await proxy_server.run(
                        parent_read,
                        parent_write,
                        proxy_server.create_initialization_options(),
                    )

        # End session
        self._db.end_session(self._session_id, self._call_count)

    def _build_proxy_server(self) -> Server:
        """Build the MCP server that faces the parent client."""
        server = Server(
            name=f"agentmeter:{self._server_name}",
            version="0.1.0",
        )

        # Re-export child's tools
        @server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            return self._tools

        # Forward tool calls with metering
        @server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict | None = None,
        ) -> list[TextContent]:
            result = await self._forward_tool_call(name, arguments or {})

            # Convert CallToolResult content to list for the server handler
            return result.content

        # Forward resources if child supports them
        @server.list_resources()
        async def handle_list_resources():
            if self._client_session is None:
                return []
            try:
                result = await self._client_session.list_resources()
                return result.resources
            except Exception:
                return []

        @server.read_resource()
        async def handle_read_resource(uri) -> ReadResourceResult | str:
            if self._client_session is None:
                return "No connection to server"
            result = await self._client_session.read_resource(uri)
            return result

        # Forward prompts if child supports them
        @server.list_prompts()
        async def handle_list_prompts():
            if self._client_session is None:
                return []
            try:
                result = await self._client_session.list_prompts()
                return result.prompts
            except Exception:
                return []

        @server.get_prompt()
        async def handle_get_prompt(name: str, arguments: dict | None = None):
            if self._client_session is None:
                return GetPromptResult(messages=[])
            result = await self._client_session.get_prompt(name, arguments)
            return result

        return server

    async def _forward_tool_call(
        self,
        name: str,
        arguments: dict,
    ) -> CallToolResult:
        """Forward a tool call to child server and record metrics."""
        if self._client_session is None:
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text="AgentMeter: no connection to server",
                )],
                isError=True,
            )

        # Serialize arguments for logging
        args_json = json.dumps(arguments, default=str)
        started_at = datetime.now().isoformat()
        start_time = perf_counter()

        try:
            result = await self._client_session.call_tool(name, arguments)
            elapsed_ms = int((perf_counter() - start_time) * 1000)

            # Serialize result for logging
            result_text = ""
            for content in result.content:
                if hasattr(content, "text"):
                    result_text += content.text
            result_size = len(result_text.encode("utf-8"))

            # Truncate stored result to avoid bloating the DB
            stored_result = (
                result_text[:2000] if len(result_text) > 2000
                else result_text
            )

            # Record the call
            call = ToolCall(
                session_id=self._session_id,
                server_name=self._server_name,
                tool_name=name,
                arguments_json=args_json[:1000],
                result_json=stored_result,
                result_size=result_size,
                is_error=bool(result.isError),
                started_at=started_at,
                elapsed_ms=elapsed_ms,
            )
            self._db.record_call(call)
            self._call_count += 1

            # Log to stderr so it doesn't interfere with MCP stdio
            _log(
                f"[{self._server_name}] {name} "
                f"{'ERROR' if result.isError else 'OK'} "
                f"{elapsed_ms}ms {result_size}B"
            )

            return result

        except Exception as exc:
            elapsed_ms = int((perf_counter() - start_time) * 1000)

            call = ToolCall(
                session_id=self._session_id,
                server_name=self._server_name,
                tool_name=name,
                arguments_json=args_json[:1000],
                result_json=str(exc)[:1000],
                result_size=0,
                is_error=True,
                started_at=started_at,
                elapsed_ms=elapsed_ms,
            )
            self._db.record_call(call)
            self._call_count += 1

            _log(f"[{self._server_name}] {name} EXCEPTION {elapsed_ms}ms: {exc}")

            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"AgentMeter: tool error: {exc}",
                )],
                isError=True,
            )


def _log(message: str) -> None:
    """Log to stderr to avoid interfering with MCP stdio protocol."""
    print(f"agentmeter: {message}", file=sys.stderr, flush=True)


def run_proxy(
    command: str,
    args: list[str],
    server_name: str = "",
    env: dict[str, str] | None = None,
) -> None:
    """Run the AgentMeter proxy (blocking entry point)."""
    proxy = AgentMeterProxy(
        command=command,
        args=args,
        server_name=server_name,
        env=env,
    )
    anyio.run(proxy.run)
