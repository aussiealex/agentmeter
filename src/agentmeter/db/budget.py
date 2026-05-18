"""Budget CRUD and enforcement operations for AgentMeter."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from agentmeter.db._helpers import build_where
from agentmeter.models import Budget


def set_budget(conn: sqlite3.Connection, budget: Budget) -> int:
    """Create or replace a budget rule. Returns the row ID."""
    conn.execute(
        "DELETE FROM budget WHERE scope = ? AND server_name = ?",
        (budget.scope, budget.server_name),
    )
    cursor = conn.execute(
        "INSERT INTO budget (scope, server_name, max_calls, action, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            budget.scope,
            budget.server_name,
            budget.max_calls,
            budget.action,
            budget.created_at,
        ),
    )
    conn.commit()
    return cursor.lastrowid or 0


def get_budgets(conn: sqlite3.Connection) -> list[Budget]:
    """Get all budget rules."""
    rows = conn.execute(
        "SELECT * FROM budget ORDER BY scope, server_name"
    ).fetchall()
    return [
        Budget(
            id=r["id"],
            scope=r["scope"],
            server_name=r["server_name"],
            max_calls=r["max_calls"],
            action=r["action"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def clear_budget(
    conn: sqlite3.Connection,
    scope: str | None = None,
    server_name: str | None = None,
) -> int:
    """Remove budget rules. Returns count of rules removed."""
    clauses: list[str] = []
    params: list[str] = []

    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if server_name is not None:
        clauses.append("server_name = ?")
        params.append(server_name)

    where = build_where(clauses)
    query = "DELETE FROM budget " + where

    cursor = conn.execute(query, params)
    conn.commit()
    return cursor.rowcount


def check_budget(
    conn: sqlite3.Connection,
    session_id: str,
    server_name: str,
) -> Budget | None:
    """Check if any budget rule would deny the next call.

    Returns the first violated Budget with action='deny', or None if OK.
    """
    budgets = conn.execute(
        "SELECT * FROM budget WHERE action = 'deny'",
    ).fetchall()

    for b in budgets:
        if b["server_name"] and b["server_name"] != server_name:
            continue

        if b["scope"] == "session":
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM tool_call "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if count and count["cnt"] >= b["max_calls"]:
                return Budget(
                    id=b["id"],
                    scope=b["scope"],
                    server_name=b["server_name"],
                    max_calls=b["max_calls"],
                    action=b["action"],
                )

        elif b["scope"] == "daily":
            today = datetime.now().strftime("%Y-%m-%d")
            clauses = ["created_at >= ?"]
            params: list = [today]
            if b["server_name"]:
                clauses.append("server_name = ?")
                params.append(b["server_name"])
            where = build_where(clauses)
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM tool_call " + where,
                params,
            ).fetchone()
            if count and count["cnt"] >= b["max_calls"]:
                return Budget(
                    id=b["id"],
                    scope=b["scope"],
                    server_name=b["server_name"],
                    max_calls=b["max_calls"],
                    action=b["action"],
                )

    return None


def get_budget_warnings(
    conn: sqlite3.Connection,
    session_id: str,
    server_name: str,
) -> list[Budget]:
    """Get budget rules with action='warn' that are at or over limit."""
    budgets = conn.execute(
        "SELECT * FROM budget WHERE action = 'warn'",
    ).fetchall()

    warnings: list[Budget] = []
    for b in budgets:
        if b["server_name"] and b["server_name"] != server_name:
            continue

        if b["scope"] == "session":
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM tool_call "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if count and count["cnt"] >= b["max_calls"]:
                warnings.append(Budget(
                    id=b["id"], scope=b["scope"],
                    server_name=b["server_name"],
                    max_calls=b["max_calls"], action=b["action"],
                ))

        elif b["scope"] == "daily":
            today = datetime.now().strftime("%Y-%m-%d")
            clauses = ["created_at >= ?"]
            params: list = [today]
            if b["server_name"]:
                clauses.append("server_name = ?")
                params.append(b["server_name"])
            where = build_where(clauses)
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM tool_call " + where,
                params,
            ).fetchone()
            if count and count["cnt"] >= b["max_calls"]:
                warnings.append(Budget(
                    id=b["id"], scope=b["scope"],
                    server_name=b["server_name"],
                    max_calls=b["max_calls"], action=b["action"],
                ))

    return warnings
