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


@dataclass
class ServerDistribution:
    """Percentile distribution of session metrics for a server."""

    server_name: str = ""
    session_count: int = 0
    p50_calls: int = 0
    p90_calls: int = 0
    p99_calls: int = 0
    p50_elapsed_ms: int = 0
    p90_elapsed_ms: int = 0
    p99_elapsed_ms: int = 0
    p50_result_bytes: int = 0
    p90_result_bytes: int = 0
    p99_result_bytes: int = 0


@dataclass
class DailyTotal:
    """Aggregated tool call stats for a single day."""

    day: str = ""
    call_count: int = 0
    error_count: int = 0
    total_elapsed_ms: int = 0


@dataclass
class BreakerTrip:
    """A recorded circuit breaker trip event."""

    id: int | None = None
    server_name: str = ""
    call_count: int = 0
    window_seconds: int = 0
    tripped_at: str = ""
    resolved_at: str | None = None


@dataclass
class Budget:
    """A budget rule that limits tool call volume.

    Scopes:
      - session: limit applies per proxy run
      - daily: limit resets each calendar day
    Server_name "" means the rule applies to all servers.
    """

    id: int | None = None
    scope: str = "daily"         # "session" or "daily"
    server_name: str = ""        # "" = all servers
    max_calls: int = 0
    action: str = "deny"         # "deny" or "warn"
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(),
    )


@dataclass
class BreakerConfig:
    """Circuit breaker configuration.

    Trips when call rate exceeds max_calls within window_seconds.
    Once tripped, blocks all calls for cooldown_seconds.
    """

    id: int | None = None
    server_name: str = ""        # "" = all servers
    max_calls: int = 20          # calls within window to trip
    window_seconds: int = 60     # rolling window size
    cooldown_seconds: int = 300  # block duration after trip
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(),
    )
