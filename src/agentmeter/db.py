"""SQLite storage for AgentMeter metering data."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from agentmeter.models import Session, SessionStats, ToolCall, ToolStats


def _default_db_path() -> Path:
    """Get DB path from env or use default."""
    env_path = os.environ.get("AGENTMETER_DB")
    if env_path:
        return Path(env_path)
    return Path.home() / ".local" / "share" / "agentmeter" / "agentmeter.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id              TEXT PRIMARY KEY,
    server_name     TEXT NOT NULL,
    server_command  TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    total_calls     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tool_call (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES session(id),
    server_name     TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    arguments_json  TEXT NOT NULL DEFAULT '',
    result_json     TEXT NOT NULL DEFAULT '',
    result_size     INTEGER NOT NULL DEFAULT 0,
    is_error        INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tool_call_session ON tool_call(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_call_tool_name ON tool_call(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_call_created_at ON tool_call(created_at);
CREATE INDEX IF NOT EXISTS idx_tool_call_server_name ON tool_call(server_name);
"""


class MeterDB:
    """SQLite database for metering records."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Session operations ──────────────────────────────────────────

    def create_session(self, session: Session) -> None:
        self._conn.execute(
            "INSERT INTO session (id, server_name, server_command, started_at) "
            "VALUES (?, ?, ?, ?)",
            (
                session.id, session.server_name,
                session.server_command, session.started_at,
            ),
        )
        self._conn.commit()

    def end_session(self, session_id: str, total_calls: int) -> None:
        self._conn.execute(
            "UPDATE session SET ended_at = ?, total_calls = ? WHERE id = ?",
            (datetime.now().isoformat(), total_calls, session_id),
        )
        self._conn.commit()

    # ── Tool call operations ────────────────────────────────────────

    def record_call(self, call: ToolCall) -> None:
        self._conn.execute(
            "INSERT INTO tool_call "
            "(session_id, server_name, tool_name, arguments_json, result_json, "
            "result_size, is_error, started_at, elapsed_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )
        self._conn.commit()

    # ── Query operations ────────────────────────────────────────────

    def get_tool_stats(
        self,
        since: str | None = None,
        server_name: str | None = None,
    ) -> list[ToolStats]:
        """Get aggregated stats per tool, optionally filtered by time and server."""
        where_clauses = []
        params: list[str] = []

        if since:
            where_clauses.append("created_at >= ?")
            params.append(since)
        if server_name:
            where_clauses.append("server_name = ?")
            params.append(server_name)

        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        rows = self._conn.execute(
            f"SELECT tool_name, "
            f"COUNT(*) as call_count, "
            f"SUM(is_error) as error_count, "
            f"SUM(elapsed_ms) as total_elapsed_ms, "
            f"AVG(elapsed_ms) as avg_elapsed_ms, "
            f"SUM(result_size) as total_result_size "
            f"FROM tool_call {where} "
            f"GROUP BY tool_name "
            f"ORDER BY call_count DESC",
            params,
        ).fetchall()

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

    def get_session_stats(
        self,
        since: str | None = None,
        limit: int = 20,
    ) -> list[SessionStats]:
        """Get per-session stats with tool breakdowns."""
        where = f"WHERE s.started_at >= '{since}'" if since else ""

        sessions = self._conn.execute(
            f"SELECT s.id, s.server_name, s.started_at, s.total_calls, "
            f"COALESCE(SUM(tc.is_error), 0) as total_errors, "
            f"COALESCE(SUM(tc.elapsed_ms), 0) as total_elapsed_ms "
            f"FROM session s "
            f"LEFT JOIN tool_call tc ON tc.session_id = s.id "
            f"{where} "
            f"GROUP BY s.id "
            f"ORDER BY s.started_at DESC "
            f"LIMIT ?",
            (limit,),
        ).fetchall()

        results = []
        for s in sessions:
            tool_rows = self._conn.execute(
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
                    server_name=s["server_name"],
                    started_at=s["started_at"],
                    total_calls=s["total_calls"] or 0,
                    total_errors=s["total_errors"],
                    total_elapsed_ms=s["total_elapsed_ms"],
                    tools=tools,
                )
            )

        return results

    def get_recent_calls(
        self,
        limit: int = 50,
        tool_name: str | None = None,
    ) -> list[ToolCall]:
        """Get recent individual tool calls."""
        where = f"WHERE tool_name = '{tool_name}'" if tool_name else ""

        rows = self._conn.execute(
            f"SELECT * FROM tool_call {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

        return [
            ToolCall(
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
            )
            for r in rows
        ]

    def get_daily_totals(self, days: int = 7) -> list[dict]:
        """Get daily call counts and elapsed time."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._conn.execute(
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
            {
                "day": r["day"],
                "call_count": r["call_count"],
                "error_count": r["error_count"] or 0,
                "total_elapsed_ms": r["total_elapsed_ms"] or 0,
            }
            for r in rows
        ]

    def get_total_calls(self, since: str | None = None) -> int:
        where = f"WHERE created_at >= '{since}'" if since else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM tool_call {where}"
        ).fetchone()
        return row["cnt"] if row else 0
