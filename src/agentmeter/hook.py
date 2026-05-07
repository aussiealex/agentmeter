"""PostToolUse hook for metering Claude Code's built-in tools.

Reads JSON from stdin (Claude Code hook protocol), writes a tool_call
row to the same AgentMeter DB used by the MCP proxy. Skips mcp__*
tools to avoid double-counting with the proxy.

Install via: agentmeter hook install
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall

SERVER_NAME = "claude-code"


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    tool_name = data.get("tool_name", "")

    # MCP tools are already metered by the proxy — skip to avoid double-counting
    if tool_name.startswith("mcp__"):
        return

    session_id = data.get("session_id", "unknown")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")

    # Capture working directory for per-project analytics
    cwd = os.environ.get("PWD", os.getcwd())

    if isinstance(tool_input, dict):
        args_str = json.dumps(tool_input)
    else:
        args_str = str(tool_input)

    if isinstance(tool_response, dict):
        result_str = json.dumps(tool_response)
    else:
        result_str = str(tool_response)

    db = MeterDB()

    # Create session on first call (idempotent — INSERT OR IGNORE)
    db.ensure_session(
        Session(
            id=session_id,
            server_name=SERVER_NAME,
            server_command=cwd,
            started_at=datetime.now().isoformat(),
        ),
    )

    db.record_call(
        ToolCall(
            session_id=session_id,
            server_name=SERVER_NAME,
            tool_name=tool_name,
            arguments_json=args_str[:1000],
            result_json=result_str[:2000],
            result_size=len(result_str),
            is_error=False,
            started_at=datetime.now().isoformat(),
            elapsed_ms=0,
        ),
    )

    db.close()


if __name__ == "__main__":
    main()
