"""OpenAI Codex CLI PostToolUse hook adapter.

Reads JSON from stdin (Codex CLI hook protocol), normalises it into
a NormalisedToolEvent, and records it via the shared base module.

Codex PostToolUse payload (common + event-specific fields):
    session_id, cwd, hook_event_name, model, permission_mode,
    turn_id, tool_name, tool_use_id, tool_input, tool_response

Entry point: python3 -m agentmeter.hooks.codex
"""

from __future__ import annotations

import json
import sys

from agentmeter.hooks.base import extract_project, get_timestamp, record_event
from agentmeter.models import NormalisedToolEvent

AGENT = "codex-cli"


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    tool_name = data.get("tool_name", "")

    # Skip MCP tools if also proxied
    if tool_name.startswith("mcp__"):
        return

    session_id = data.get("session_id", "unknown")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")
    cwd = data.get("cwd", "")
    model_id = data.get("model", "")

    # Serialise input
    if isinstance(tool_input, dict):
        args_str = json.dumps(tool_input)
    else:
        args_str = str(tool_input)

    # Serialise response
    if isinstance(tool_response, dict):
        result_str = json.dumps(tool_response)
    else:
        result_str = str(tool_response)

    event = NormalisedToolEvent(
        session_id=session_id,
        agent=AGENT,
        tool_name=tool_name,
        input_size=len(args_str),
        result_size=len(result_str),
        result_type="success",  # Codex PostToolUse fires after completion
        model_id=model_id,
        project=extract_project(cwd),
        cwd=cwd,
        timestamp=get_timestamp(),
        elapsed_ms=0,
        arguments_json=args_str[:1000],
        result_json=result_str[:2000],
    )

    record_event(event)


if __name__ == "__main__":
    main()
