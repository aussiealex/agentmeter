"""Gemini CLI AfterTool hook adapter.

Reads JSON from stdin (Gemini CLI hook protocol), normalises it into
a NormalisedToolEvent, and records it via the shared base module.

Gemini AfterTool payload (common + event-specific fields):
    session_id, cwd, timestamp, hook_event_name,
    tool_name, tool_input, tool_response (llmContent, error),
    mcp_context, original_request_name

Entry point: python3 -m agentmeter.hooks.gemini
"""

from __future__ import annotations

import json
import sys

from agentmeter.hooks.base import extract_project, get_timestamp, record_event
from agentmeter.models import NormalisedToolEvent

AGENT = "gemini-cli"


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
    tool_response = data.get("tool_response", {})
    cwd = data.get("cwd", "")
    timestamp = data.get("timestamp", "") or get_timestamp()

    # Serialise input
    if isinstance(tool_input, dict):
        args_str = json.dumps(tool_input)
    else:
        args_str = str(tool_input)

    # Extract response content — Gemini nests under llmContent
    if isinstance(tool_response, dict):
        llm_content = tool_response.get("llmContent", "")
        error = tool_response.get("error")
        if llm_content:
            result_str = (
                json.dumps(llm_content) if isinstance(llm_content, dict)
                else str(llm_content)
            )
        elif error:
            result_str = json.dumps(error) if isinstance(error, dict) else str(error)
        else:
            result_str = json.dumps(tool_response)
    else:
        error = None
        result_str = str(tool_response)

    result_type = "error" if error else "success"

    event = NormalisedToolEvent(
        session_id=session_id,
        agent=AGENT,
        tool_name=tool_name,
        input_size=len(args_str),
        result_size=len(result_str),
        result_type=result_type,
        model_id="",  # Not available in AfterTool — comes from AfterModel
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
