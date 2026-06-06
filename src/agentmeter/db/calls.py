"""Tool call recording and query operations for AgentMeter."""

from __future__ import annotations

import sqlite3

from agentmeter.db._helpers import build_where
from agentmeter.models import ToolCall, ToolStats


def _row_to_tool_call(r: sqlite3.Row) -> ToolCall:
    return ToolCall(
        id=r["id"],
        session_id=r["session_id"],
        server_name=r["server_name"],
        tool_name=r["tool_name"],
        arguments_json=r["arguments_json"],
        result_json=r["result_json"],
        result_size=r["result_size"],
        is_error=bool(r["is_error"]),
        started_at=r["started_at"],
        elapsed_ms=r["elapsed_ms"],
        created_at=r["created_at"],
        agent=r["agent"],
        project=r["project"],
        model_id=r["model_id"],
        input_size=r["input_size"],
    )


def record_call(conn: sqlite3.Connection, call: ToolCall) -> None:
    conn.execute(
        "INSERT INTO tool_call "
        "(session_id, server_name, tool_name, arguments_json, result_json, "
        "result_size, is_error, started_at, elapsed_ms, created_at, "
        "agent, project, model_id, input_size) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            call.session_id,
            call.server_name,
            call.tool_name,
            call.arguments_json,
            call.result_json,
            call.result_size,
            int(call.is_error),
            call.started_at,
            call.elapsed_ms,
            call.created_at,
            call.agent,
            call.project,
            call.model_id,
            call.input_size,
        ),
    )
    conn.commit()


def get_tool_stats(
    conn: sqlite3.Connection,
    since: str | None = None,
    server_name: str | None = None,
    project: str | None = None,
) -> list[ToolStats]:
    """Get aggregated stats per tool, optionally filtered."""
    clauses: list[str] = []
    params: list[str] = []

    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if server_name:
        clauses.append("server_name = ?")
        params.append(server_name)
    if project:
        clauses.append("project = ?")
        params.append(project)

    where = build_where(clauses)

    query = (
        "SELECT tool_name, "
        "COUNT(*) as call_count, "
        "SUM(is_error) as error_count, "
        "SUM(elapsed_ms) as total_elapsed_ms, "
        "AVG(elapsed_ms) as avg_elapsed_ms, "
        "SUM(result_size) as total_result_size "
        "FROM tool_call " + where + " "
        "GROUP BY tool_name "
        "ORDER BY call_count DESC"
    )

    rows = conn.execute(query, params).fetchall()

    return [
        ToolStats(
            tool_name=r["tool_name"],
            call_count=r["call_count"],
            error_count=r["error_count"] or 0,
            total_elapsed_ms=r["total_elapsed_ms"] or 0,
            avg_elapsed_ms=r["avg_elapsed_ms"] or 0.0,
            total_result_size=r["total_result_size"] or 0,
        )
        for r in rows
    ]


def get_recent_calls(
    conn: sqlite3.Connection,
    limit: int = 50,
    tool_name: str | None = None,
    project: str | None = None,
    since: str | None = None,
) -> list[ToolCall]:
    """Get recent individual tool calls."""
    clauses: list[str] = []
    params: list = []

    if tool_name:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if project:
        clauses.append("project = ?")
        params.append(project)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)

    where = build_where(clauses)
    params.append(limit)

    query = (
        "SELECT * FROM tool_call " + where + " "
        "ORDER BY created_at DESC LIMIT ?"
    )

    rows = conn.execute(query, params).fetchall()

    return [_row_to_tool_call(r) for r in rows]


def get_calls_for_export(
    conn: sqlite3.Connection,
    since: str | None = None,
    tool_name: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
    project: str | None = None,
) -> list[ToolCall]:
    """Get tool calls for JSONL export, with optional filters."""
    clauses: list[str] = []
    params: list = []

    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if tool_name:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if project:
        clauses.append("project = ?")
        params.append(project)

    where = build_where(clauses)
    query = "SELECT * FROM tool_call " + where + " ORDER BY created_at ASC"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [_row_to_tool_call(r) for r in rows]


def get_total_calls(
    conn: sqlite3.Connection, since: str | None = None,
) -> int:
    clauses: list[str] = []
    params: list = []

    if since:
        clauses.append("created_at >= ?")
        params.append(since)

    where = build_where(clauses)
    query = "SELECT COUNT(*) as cnt FROM tool_call " + where

    row = conn.execute(query, params).fetchone()
    return row["cnt"] if row else 0
