"""Claude Code PostToolUse hook adapter.

Reads JSON from stdin (Claude Code hook protocol), normalises it into
a NormalisedToolEvent, and records it via the shared base module.

Skips mcp__* tools to avoid double-counting with the MCP proxy.

Entry point: python3 -m agentmeter.hooks.claude
"""

from __future__ import annotations

import json
import os
import sys

from agentmeter.hooks.base import extract_project, get_cwd, get_timestamp, record_event
from agentmeter.models import NormalisedToolEvent

AGENT = "claude-code"


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    tool_name = data.get("tool_name", "")

    # MCP tools are already metered by the proxy — skip
    if tool_name.startswith("mcp__"):
        return

    session_id = data.get("session_id", "unknown")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")

    cwd = get_cwd()

    if isinstance(tool_input, dict):
        args_str = json.dumps(tool_input)
    else:
        args_str = str(tool_input)

    if isinstance(tool_response, dict):
        result_str = json.dumps(tool_response)
    else:
        result_str = str(tool_response)

    # Check for model ID in environment
    model_id = os.environ.get("CLAUDE_MODEL", "")

    event = NormalisedToolEvent(
        session_id=session_id,
        agent=AGENT,
        tool_name=tool_name,
        input_size=len(args_str),
        result_size=len(result_str),
        result_type="success",  # PostToolUse only fires on success
        model_id=model_id,
        project=extract_project(cwd),
        cwd=cwd,
        timestamp=get_timestamp(),
        elapsed_ms=0,  # Not available from PostToolUse alone
        arguments_json=args_str[:1000],
        result_json=result_str[:2000],
    )

    record_event(event)


if __name__ == "__main__":
    main()
