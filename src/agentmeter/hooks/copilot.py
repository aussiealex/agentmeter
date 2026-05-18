"""GitHub Copilot CLI postToolUse hook adapter.

Reads JSON from stdin (Copilot CLI hook protocol), normalises it into
a NormalisedToolEvent, and records it via the shared base module.

Copilot supports two payload formats — snake_case (VS Code compatible)
and camelCase. This adapter handles both.

Snake_case payload:
    session_id, timestamp, cwd, hook_event_name,
    tool_name, tool_input, tool_result (result_type, text_result_for_llm)

CamelCase payload:
    sessionId, timestamp, cwd, toolName, toolArgs,
    toolResult (resultType, textResultForLlm)

Entry point: python3 -m agentmeter.hooks.copilot
"""

from __future__ import annotations

import json
import sys

from agentmeter.hooks.base import extract_project, get_timestamp, record_event
from agentmeter.models import NormalisedToolEvent

AGENT = "copilot-cli"


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    # Handle both camelCase and snake_case formats
    tool_name = data.get("tool_name") or data.get("toolName", "")

    # Skip MCP tools if also proxied
    if tool_name.startswith("mcp__"):
        return

    session_id = data.get("session_id") or data.get("sessionId", "unknown")
    tool_input = data.get("tool_input") or data.get("toolArgs", {})
    tool_result = data.get("tool_result") or data.get("toolResult", {})
    cwd = data.get("cwd", "")

    # Extract timestamp — Copilot may send ms epoch (number) or ISO string
    raw_ts = data.get("timestamp", "")
    if isinstance(raw_ts, (int, float)):
        from datetime import UTC, datetime
        timestamp = datetime.fromtimestamp(raw_ts / 1000, tz=UTC).isoformat()
    elif isinstance(raw_ts, str) and raw_ts:
        timestamp = raw_ts
    else:
        timestamp = get_timestamp()

    # Serialise input
    if isinstance(tool_input, dict):
        args_str = json.dumps(tool_input)
    else:
        args_str = str(tool_input)

    # Extract result — Copilot nests under text_result_for_llm / textResultForLlm
    if isinstance(tool_result, dict):
        result_str = (
            tool_result.get("text_result_for_llm")
            or tool_result.get("textResultForLlm")
            or json.dumps(tool_result)
        )
        result_type = (
            tool_result.get("result_type")
            or tool_result.get("resultType")
            or "success"
        )
    else:
        result_str = str(tool_result)
        result_type = "success"

    event = NormalisedToolEvent(
        session_id=session_id,
        agent=AGENT,
        tool_name=tool_name,
        input_size=len(args_str),
        result_size=len(result_str),
        result_type=result_type,
        model_id="",  # Not available in Copilot postToolUse
        project=extract_project(cwd),
        cwd=cwd,
        timestamp=timestamp,
        elapsed_ms=0,
        arguments_json=args_str[:1000],
        result_json=result_str[:2000],
    )

    record_event(event)


if __name__ == "__main__":
    main()
