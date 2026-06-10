"""Shared hook logic: NormalisedToolEvent → DB recording.

All agent adapters call record_event() after normalising their payload.
This module handles session creation and tool call recording.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from agentmeter.db import MeterDB
from agentmeter.models import NormalisedToolEvent, Session, ToolCall

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def _safe_session_id(sid: str) -> str | None:
    """Return session_id only if it's safe for use in file paths."""
    if sid and _SAFE_ID.match(sid):
        return sid
    return None


def record_event(event: NormalisedToolEvent) -> None:
    """Record a normalised tool event to the database."""
    db = MeterDB()
    try:
        db.ensure_session(
            Session(
                id=event.session_id,
                server_name=event.agent,
                server_command=event.cwd,
                started_at=event.timestamp,
            ),
        )

        db.record_call(
            ToolCall(
                session_id=event.session_id,
                server_name=event.agent,
                tool_name=event.tool_name,
                arguments_json=event.arguments_json,
                result_json=event.result_json,
                result_size=event.result_size,
                is_error=event.result_type != "success",
                started_at=event.timestamp,
                elapsed_ms=event.elapsed_ms,
                agent=event.agent,
                project=event.project,
                model_id=event.model_id,
                input_size=event.input_size,
            ),
        )
    except Exception as exc:
        # Never crash the agent — log and exit cleanly
        print(f"agentmeter: hook error: {exc}", file=sys.stderr, flush=True)
    finally:
        db.close()

    # Update coach state file for yellow card tracking
    _update_coach_state(event)


def _update_coach_state(event: NormalisedToolEvent) -> None:
    """Increment the coach state file for this session.

    <1ms: read JSON, bump counters, write JSON. No DB queries.
    """
    try:
        sid = _safe_session_id(event.session_id)
        if not sid:
            return

        from agentmeter.platform import data_dir

        coach_dir = data_dir() / "coach"
        state_path = coach_dir / f"{sid}.json"

        state: dict = {}
        if state_path.exists():
            state = json.loads(state_path.read_text())

        state["calls"] = state.get("calls", 0) + 1
        tools = state.get("tools", {})
        tools[event.tool_name] = tools.get(event.tool_name, 0) + 1
        state["tools"] = tools
        state["last_tool_at"] = event.timestamp
        if "started_at" not in state:
            state["started_at"] = event.timestamp
        if "warnings_fired" not in state:
            state["warnings_fired"] = []

        coach_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))
    except Exception:
        pass  # Never block the agent for coaching


def extract_project(cwd: str) -> str:
    """Extract a project name from a working directory path.

    Uses the directory name as the project identifier.
    E.g. "/media/aa/LargeBackup/MainApps/AgentMeter" → "AgentMeter"
    """
    if not cwd:
        return ""
    return Path(cwd).name


def get_cwd() -> str:
    """Get the current working directory, preferring PWD env var."""
    return os.environ.get("PWD", os.getcwd())


def get_timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now().isoformat()
