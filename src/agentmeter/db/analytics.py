"""Aggregate queries and distribution analytics for AgentMeter."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta

from agentmeter.db._helpers import build_where
from agentmeter.models import (
    DailyTotal,
    ServerDistribution,
    SessionStats,
    ToolStats,
)


def get_session_stats(
    conn: sqlite3.Connection,
    since: str | None = None,
    limit: int = 20,
) -> list[SessionStats]:
    """Get per-session stats with tool breakdowns."""
    clauses: list[str] = []
    params: list = []

    if since:
        clauses.append("s.started_at >= ?")
        params.append(since)

    where = build_where(clauses)
    params.append(limit)

    query = (
        "SELECT s.id, s.name, s.server_name, s.started_at, s.total_calls, "
        "COALESCE(SUM(tc.is_error), 0) as total_errors, "
        "COALESCE(SUM(tc.elapsed_ms), 0) as total_elapsed_ms "
        "FROM session s "
        "LEFT JOIN tool_call tc ON tc.session_id = s.id "
        + where + " "
        "GROUP BY s.id "
        "ORDER BY s.started_at DESC "
        "LIMIT ?"
    )

    sessions = conn.execute(query, params).fetchall()

    results = []
    for s in sessions:
        tool_rows = conn.execute(
            "SELECT tool_name, COUNT(*) as call_count, "
            "SUM(is_error) as error_count, "
            "SUM(elapsed_ms) as total_elapsed_ms, "
            "AVG(elapsed_ms) as avg_elapsed_ms, "
            "SUM(result_size) as total_result_size "
            "FROM tool_call WHERE session_id = ? "
            "GROUP BY tool_name ORDER BY call_count DESC",
            (s["id"],),
        ).fetchall()

        tools = [
            ToolStats(
                tool_name=r["tool_name"],
                call_count=r["call_count"],
                error_count=r["error_count"] or 0,
                total_elapsed_ms=r["total_elapsed_ms"] or 0,
                avg_elapsed_ms=r["avg_elapsed_ms"] or 0.0,
                total_result_size=r["total_result_size"] or 0,
            )
            for r in tool_rows
        ]

        results.append(
            SessionStats(
                session_id=s["id"],
                session_name=s["name"] or s["id"],
                server_name=s["server_name"],
                started_at=s["started_at"],
                total_calls=s["total_calls"] or 0,
                total_errors=s["total_errors"],
                total_elapsed_ms=s["total_elapsed_ms"],
                tools=tools,
            )
        )

    return results


def get_daily_totals(
    conn: sqlite3.Connection, days: int = 7,
) -> list[DailyTotal]:
    """Get daily call counts and elapsed time."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT DATE(created_at) as day, "
        "COUNT(*) as call_count, "
        "SUM(is_error) as error_count, "
        "SUM(elapsed_ms) as total_elapsed_ms "
        "FROM tool_call WHERE created_at >= ? "
        "GROUP BY DATE(created_at) "
        "ORDER BY day",
        (since,),
    ).fetchall()

    return [
        DailyTotal(
            day=r["day"],
            call_count=r["call_count"],
            error_count=r["error_count"] or 0,
            total_elapsed_ms=r["total_elapsed_ms"] or 0,
        )
        for r in rows
    ]


def get_session_distribution(
    conn: sqlite3.Connection,
    server_name: str | None = None,
) -> list[ServerDistribution]:
    """Get percentile distributions of session metrics, grouped by server."""
    clauses: list[str] = []
    params: list[str] = []

    if server_name:
        clauses.append("s.server_name = ?")
        params.append(server_name)

    where = build_where(clauses)

    query = (
        "SELECT s.server_name, s.id, "
        "COALESCE(s.total_calls, 0) as calls, "
        "COALESCE(SUM(tc.elapsed_ms), 0) as elapsed_ms, "
        "COALESCE(SUM(tc.result_size), 0) as result_size "
        "FROM session s "
        "LEFT JOIN tool_call tc ON tc.session_id = s.id "
        + where + " "
        "GROUP BY s.id "
        "ORDER BY s.server_name"
    )

    rows = conn.execute(query, params).fetchall()

    servers: dict[str, list[dict]] = {}
    for r in rows:
        name = r["server_name"]
        servers.setdefault(name, []).append({
            "calls": r["calls"],
            "elapsed_ms": r["elapsed_ms"],
            "result_size": r["result_size"],
        })

    results = []
    for srv, sessions in sorted(servers.items()):
        n = len(sessions)
        calls = sorted(s["calls"] for s in sessions)
        elapsed = sorted(s["elapsed_ms"] for s in sessions)
        sizes = sorted(s["result_size"] for s in sessions)

        results.append(ServerDistribution(
            server_name=srv,
            session_count=n,
            p50_calls=_percentile(calls, 50),
            p90_calls=_percentile(calls, 90),
            p99_calls=_percentile(calls, 99),
            p50_elapsed_ms=_percentile(elapsed, 50),
            p90_elapsed_ms=_percentile(elapsed, 90),
            p99_elapsed_ms=_percentile(elapsed, 99),
            p50_result_bytes=_percentile(sizes, 50),
            p90_result_bytes=_percentile(sizes, 90),
            p99_result_bytes=_percentile(sizes, 99),
        ))

    return results


def _percentile(sorted_values: list[int], pct: int) -> int:
    """Compute the nearest-rank percentile from a sorted list."""
    if not sorted_values:
        return 0
    rank = math.ceil(pct / 100 * len(sorted_values))
    return sorted_values[max(0, rank - 1)]
