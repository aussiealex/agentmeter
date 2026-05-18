"""Circuit breaker CRUD and trip operations for AgentMeter."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from agentmeter.models import BreakerConfig, BreakerTrip


def set_breaker(conn: sqlite3.Connection, config: BreakerConfig) -> int:
    """Create or replace a circuit breaker config. Returns row ID."""
    conn.execute(
        "DELETE FROM breaker WHERE server_name = ?",
        (config.server_name,),
    )
    cursor = conn.execute(
        "INSERT INTO breaker "
        "(server_name, max_calls, window_seconds, "
        "cooldown_seconds, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            config.server_name,
            config.max_calls,
            config.window_seconds,
            config.cooldown_seconds,
            config.created_at,
        ),
    )
    conn.commit()
    return cursor.lastrowid or 0


def get_breakers(conn: sqlite3.Connection) -> list[BreakerConfig]:
    """Get all circuit breaker configs."""
    rows = conn.execute(
        "SELECT * FROM breaker ORDER BY server_name"
    ).fetchall()
    return [
        BreakerConfig(
            id=r["id"],
            server_name=r["server_name"],
            max_calls=r["max_calls"],
            window_seconds=r["window_seconds"],
            cooldown_seconds=r["cooldown_seconds"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def get_breaker_for_server(
    conn: sqlite3.Connection, server_name: str,
) -> BreakerConfig | None:
    """Get the breaker config that applies to a server.

    Server-specific rules take precedence over global ("").
    """
    row = conn.execute(
        "SELECT * FROM breaker WHERE server_name = ?",
        (server_name,),
    ).fetchone()
    if row:
        return BreakerConfig(
            id=row["id"],
            server_name=row["server_name"],
            max_calls=row["max_calls"],
            window_seconds=row["window_seconds"],
            cooldown_seconds=row["cooldown_seconds"],
        )
    # Fall back to global
    row = conn.execute(
        "SELECT * FROM breaker WHERE server_name = ''",
    ).fetchone()
    if row:
        return BreakerConfig(
            id=row["id"],
            server_name=row["server_name"],
            max_calls=row["max_calls"],
            window_seconds=row["window_seconds"],
            cooldown_seconds=row["cooldown_seconds"],
        )
    return None


def clear_breakers(
    conn: sqlite3.Connection, server_name: str | None = None,
) -> int:
    """Remove breaker configs. Returns count removed."""
    if server_name is not None:
        cursor = conn.execute(
            "DELETE FROM breaker WHERE server_name = ?",
            (server_name,),
        )
    else:
        cursor = conn.execute("DELETE FROM breaker")
    conn.commit()
    return cursor.rowcount


def record_breaker_trip(
    conn: sqlite3.Connection,
    server_name: str,
    call_count: int,
    window_seconds: int,
) -> None:
    """Log a circuit breaker trip event."""
    conn.execute(
        "INSERT INTO breaker_trip "
        "(server_name, call_count, window_seconds, tripped_at) "
        "VALUES (?, ?, ?, ?)",
        (
            server_name,
            call_count,
            window_seconds,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def get_breaker_trips(
    conn: sqlite3.Connection, limit: int = 10,
) -> list[BreakerTrip]:
    """Get recent breaker trip events."""
    rows = conn.execute(
        "SELECT * FROM breaker_trip "
        "ORDER BY tripped_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        BreakerTrip(
            id=r["id"],
            server_name=r["server_name"],
            call_count=r["call_count"],
            window_seconds=r["window_seconds"],
            tripped_at=r["tripped_at"],
            resolved_at=r["resolved_at"],
        )
        for r in rows
    ]
