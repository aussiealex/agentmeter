"""Shared hook logic: NormalisedToolEvent → DB recording.

All agent adapters call record_event() after normalising their payload.
This module handles session creation and tool call recording.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from agentmeter.db import MeterDB
from agentmeter.models import NormalisedToolEvent, Session, ToolCall


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
