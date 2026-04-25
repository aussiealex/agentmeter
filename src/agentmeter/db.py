"""SQLite storage for AgentMeter metering data."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from agentmeter.models import (
    BreakerConfig,
    Budget,
    Session,
    SessionStats,
    ToolCall,
    ToolStats,
)


def _default_db_path() -> Path:
    """Get DB path from env or use default."""
    env_path = os.environ.get("AGENTMETER_DB")
    if env_path:
        return Path(env_path)
    return Path.home() / ".local" / "share" / "agentmeter" / "agentmeter.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS breaker (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_name     TEXT NOT NULL DEFAULT '',
    max_calls       INTEGER NOT NULL DEFAULT 20,
    window_seconds  INTEGER NOT NULL DEFAULT 60,
    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS breaker_trip (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_name     TEXT NOT NULL,
    call_count      INTEGER NOT NULL,
    window_seconds  INTEGER NOT NULL,
    tripped_at      TEXT NOT NULL,
    resolved_at     TEXT
);

CREATE TABLE IF NOT EXISTS budget (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL,
    server_name     TEXT NOT NULL DEFAULT '',
    max_calls       INTEGER NOT NULL,
    action          TEXT NOT NULL DEFAULT 'deny',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that may be missing from older databases."""
        columns = {
            r[1]
            for r in self._conn.execute("PRAGMA table_info(session)").fetchall()
        }
        if "name" not in columns:
            self._conn.execute(
                "ALTER TABLE session ADD COLUMN name TEXT NOT NULL DEFAULT ''"
            )

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
        name = self._generate_session_name(session_id, total_calls)
        self._conn.execute(
            "UPDATE session SET ended_at = ?, total_calls = ?, name = ? "
            "WHERE id = ?",
            (datetime.now().isoformat(), total_calls, name, session_id),
        )
        self._conn.commit()

    def rename_session(self, session_id: str, name: str) -> bool:
        """Rename a session. Returns True if the session was found."""
        cursor = self._conn.execute(
            "UPDATE session SET name = ? WHERE id = ? OR name = ?",
            (name, session_id, session_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def _generate_session_name(
        self, session_id: str, total_calls: int,
    ) -> str:
        """Generate a human-readable session name from activity."""
        # Get server name
        row = self._conn.execute(
            "SELECT server_name, started_at FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return session_id
        server = row["server_name"]
        started = row["started_at"]

        # Get top tools used (up to 2)
        top_tools = self._conn.execute(
            "SELECT tool_name, COUNT(*) as cnt FROM tool_call "
            "WHERE session_id = ? GROUP BY tool_name "
            "ORDER BY cnt DESC LIMIT 2",
            (session_id,),
        ).fetchall()

        # Build name: server-tools-count
        # e.g. "mailsift-search+fetch-12calls"
        parts = [server]

        if top_tools:
            tool_names = "+".join(r["tool_name"] for r in top_tools)
            parts.append(tool_names)

        parts.append(f"{total_calls}calls")

        # Add time context from started_at
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

    @staticmethod
    def _build_where(
        clauses: list[str],
    ) -> str:
        """Join WHERE clauses safely. All clauses must be hardcoded strings."""
        if not clauses:
            return ""
        return "WHERE " + " AND ".join(clauses)

    def get_tool_stats(
        self,
        since: str | None = None,
        server_name: str | None = None,
    ) -> list[ToolStats]:
        """Get aggregated stats per tool, optionally filtered by time and server."""
        clauses: list[str] = []
        params: list[str] = []

        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if server_name:
            clauses.append("server_name = ?")
            params.append(server_name)

        where = self._build_where(clauses)

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

        rows = self._conn.execute(query, params).fetchall()

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
        clauses: list[str] = []
        params: list = []

        if since:
            clauses.append("s.started_at >= ?")
            params.append(since)

        where = self._build_where(clauses)
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

        sessions = self._conn.execute(query, params).fetchall()

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

    def get_recent_calls(
        self,
        limit: int = 50,
        tool_name: str | None = None,
    ) -> list[ToolCall]:
        """Get recent individual tool calls."""
        clauses: list[str] = []
        params: list = []

        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)

        where = self._build_where(clauses)
        params.append(limit)

        query = (
            "SELECT * FROM tool_call " + where + " "
            "ORDER BY created_at DESC LIMIT ?"
        )

        rows = self._conn.execute(query, params).fetchall()

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

    # ── Budget operations ─────────────────────────────────────────

    def set_budget(self, budget: Budget) -> int:
        """Create or replace a budget rule. Returns the row ID."""
        # Remove existing rule with same scope + server_name
        self._conn.execute(
            "DELETE FROM budget WHERE scope = ? AND server_name = ?",
            (budget.scope, budget.server_name),
        )
        cursor = self._conn.execute(
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
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_budgets(self) -> list[Budget]:
        """Get all budget rules."""
        rows = self._conn.execute(
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
        self,
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

        where = self._build_where(clauses)
        query = "DELETE FROM budget " + where

        cursor = self._conn.execute(query, params)
        self._conn.commit()
        return cursor.rowcount

    def check_budget(
        self,
        session_id: str,
        server_name: str,
    ) -> Budget | None:
        """Check if any budget rule would deny the next call.

        Returns the first violated Budget with action='deny', or None if OK.
        """
        budgets = self._conn.execute(
            "SELECT * FROM budget WHERE action = 'deny'",
        ).fetchall()

        for b in budgets:
            # Skip rules that don't apply to this server
            if b["server_name"] and b["server_name"] != server_name:
                continue

            if b["scope"] == "session":
                count = self._conn.execute(
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
                where = self._build_where(clauses)
                count = self._conn.execute(
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
        self,
        session_id: str,
        server_name: str,
    ) -> list[Budget]:
        """Get budget rules with action='warn' that are at or over limit."""
        budgets = self._conn.execute(
            "SELECT * FROM budget WHERE action = 'warn'",
        ).fetchall()

        warnings: list[Budget] = []
        for b in budgets:
            if b["server_name"] and b["server_name"] != server_name:
                continue

            if b["scope"] == "session":
                count = self._conn.execute(
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
                where = self._build_where(clauses)
                count = self._conn.execute(
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

    # ── Circuit breaker operations ─────────────────────────────────

    def set_breaker(self, config: BreakerConfig) -> int:
        """Create or replace a circuit breaker config. Returns row ID."""
        self._conn.execute(
            "DELETE FROM breaker WHERE server_name = ?",
            (config.server_name,),
        )
        cursor = self._conn.execute(
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
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_breakers(self) -> list[BreakerConfig]:
        """Get all circuit breaker configs."""
        rows = self._conn.execute(
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
        self, server_name: str,
    ) -> BreakerConfig | None:
        """Get the breaker config that applies to a server.

        Server-specific rules take precedence over global ("").
        """
        row = self._conn.execute(
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
        row = self._conn.execute(
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
        self, server_name: str | None = None,
    ) -> int:
        """Remove breaker configs. Returns count removed."""
        if server_name is not None:
            cursor = self._conn.execute(
                "DELETE FROM breaker WHERE server_name = ?",
                (server_name,),
            )
        else:
            cursor = self._conn.execute("DELETE FROM breaker")
        self._conn.commit()
        return cursor.rowcount

    def record_breaker_trip(
        self,
        server_name: str,
        call_count: int,
        window_seconds: int,
    ) -> None:
        """Log a circuit breaker trip event."""
        self._conn.execute(
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
        self._conn.commit()

    def get_breaker_trips(self, limit: int = 10) -> list[dict]:
        """Get recent breaker trip events."""
        rows = self._conn.execute(
            "SELECT * FROM breaker_trip "
            "ORDER BY tripped_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Aggregate queries ────────────────────────────────────────

    def get_total_calls(self, since: str | None = None) -> int:
        clauses: list[str] = []
        params: list = []

        if since:
            clauses.append("created_at >= ?")
            params.append(since)

        where = self._build_where(clauses)

        query = "SELECT COUNT(*) as cnt FROM tool_call " + where

        row = self._conn.execute(query, params).fetchone()
        return row["cnt"] if row else 0
