"""Session CRUD operations for AgentMeter."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from agentmeter.models import Session


def create_session(conn: sqlite3.Connection, session: Session) -> None:
    conn.execute(
        "INSERT INTO session (id, server_name, server_command, started_at) "
        "VALUES (?, ?, ?, ?)",
        (
            session.id, session.server_name,
            session.server_command, session.started_at,
        ),
    )
    conn.commit()


def ensure_session(conn: sqlite3.Connection, session: Session) -> None:
    """Create session if it doesn't exist. Idempotent for hook use."""
    conn.execute(
        "INSERT OR IGNORE INTO session "
        "(id, server_name, server_command, started_at) "
        "VALUES (?, ?, ?, ?)",
        (
            session.id, session.server_name,
            session.server_command, session.started_at,
        ),
    )
    conn.commit()


def end_session(
    conn: sqlite3.Connection, session_id: str, total_calls: int,
) -> None:
    name = _generate_session_name(conn, session_id, total_calls)
    conn.execute(
        "UPDATE session SET ended_at = ?, total_calls = ?, name = ? "
        "WHERE id = ?",
        (datetime.now().isoformat(), total_calls, name, session_id),
    )
    conn.commit()


def rename_session(
    conn: sqlite3.Connection, session_id: str, name: str,
) -> bool:
    """Rename a session. Returns True if the session was found."""
    cursor = conn.execute(
        "UPDATE session SET name = ? WHERE id = ? OR name = ?",
        (name, session_id, session_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_sessions(
    conn: sqlite3.Connection, limit: int = 20,
) -> list[Session]:
    """Get recent sessions ordered by start time."""
    rows = conn.execute(
        "SELECT id, name, server_name, server_command, started_at, "
        "ended_at, total_calls, commits, files_changed, "
        "tests_passed, tests_failed FROM session "
        "ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_session(r) for r in rows]


def update_session_outcome(
    conn: sqlite3.Connection,
    session_id: str,
    commits: int,
    files_changed: int,
    tests_passed: int,
    tests_failed: int,
) -> bool:
    """Update session with outcome data. Returns True if found."""
    cursor = conn.execute(
        "UPDATE session SET commits = ?, files_changed = ?, "
        "tests_passed = ?, tests_failed = ? WHERE id = ?",
        (commits, files_changed, tests_passed, tests_failed,
         session_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def _row_to_session(r: sqlite3.Row) -> Session:
    # Migration columns always exist after init_schema()
    commits = r["commits"]
    files_changed = r["files_changed"]
    tests_passed = r["tests_passed"]
    tests_failed = r["tests_failed"]
    outcome = _derive_outcome(
        commits, tests_passed, tests_failed,
    )
    return Session(
        id=r["id"],
        name=r["name"],
        server_name=r["server_name"],
        server_command=r["server_command"],
        started_at=r["started_at"],
        ended_at=r["ended_at"],
        total_calls=r["total_calls"],
        commits=commits,
        files_changed=files_changed,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        outcome=outcome,
    )


def _derive_outcome(
    commits: int, tests_passed: int, tests_failed: int,
) -> str:
    """Derive a human-readable outcome label from facts."""
    if tests_failed > 0:
        return "failed"
    if tests_passed > 0 and commits > 0:
        return "tested+committed"
    if tests_passed > 0:
        return "tested"
    if commits > 0:
        return "committed"
    return ""


def _generate_session_name(
    conn: sqlite3.Connection, session_id: str, total_calls: int,
) -> str:
    """Generate a human-readable session name from activity."""
    row = conn.execute(
        "SELECT server_name, started_at FROM session WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return session_id
    server = row["server_name"]
    started = row["started_at"]

    top_tools = conn.execute(
        "SELECT tool_name, COUNT(*) as cnt FROM tool_call "
        "WHERE session_id = ? GROUP BY tool_name "
        "ORDER BY cnt DESC LIMIT 2",
        (session_id,),
    ).fetchall()

    parts = [server]

    if top_tools:
        tool_names = "+".join(r["tool_name"] for r in top_tools)
        parts.append(tool_names)

    parts.append(f"{total_calls}calls")

    try:
        hour = int(started[:13].split("T")[1]) if "T" in started else 0
    except (IndexError, ValueError):
        hour = 0

    if 5 <= hour < 12:
        parts.insert(1, "morning")
    elif 12 <= hour < 17:
        parts.insert(1, "afternoon")
    elif 17 <= hour < 21:
        parts.insert(1, "evening")
    else:
        parts.insert(1, "night")

    return "-".join(parts)
