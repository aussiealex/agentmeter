"""Database schema definition and migrations for AgentMeter."""

from __future__ import annotations

import sqlite3

SCHEMA = """
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


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and run migrations."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that may be missing from older databases."""
    columns = {
        r[1]
        for r in conn.execute("PRAGMA table_info(session)").fetchall()
    }
    if "name" not in columns:
        conn.execute(
            "ALTER TABLE session ADD COLUMN name TEXT NOT NULL DEFAULT ''"
        )
