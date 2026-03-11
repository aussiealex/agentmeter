"""Data models for AgentMeter metering records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolCall:
    """A single metered MCP tool call."""

    id: int | None = None
    session_id: str = ""
    server_name: str = ""
    tool_name: str = ""
    arguments_json: str = ""
    result_json: str = ""
    result_size: int = 0
    is_error: bool = False
    started_at: str = ""
    elapsed_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Session:
    """A metering session (one proxy run)."""

    id: str = ""
    server_name: str = ""
    server_command: str = ""
    started_at: str = ""
    name: str = ""
    ended_at: str | None = None
    total_calls: int = 0


@dataclass
class ToolStats:
    """Aggregated stats for a single tool."""

    tool_name: str = ""
    call_count: int = 0
    error_count: int = 0
    total_elapsed_ms: int = 0
    avg_elapsed_ms: float = 0.0
    total_result_size: int = 0


@dataclass
class SessionStats:
    """Aggregated stats for a session."""

    session_id: str = ""
    session_name: str = ""
    server_name: str = ""
    started_at: str = ""
    total_calls: int = 0
    total_errors: int = 0
    total_elapsed_ms: int = 0
    tools: list[ToolStats] = field(default_factory=list)
