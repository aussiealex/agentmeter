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

CREATE TABLE IF NOT EXISTS rate_card (
    model_id           TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL DEFAULT '',
    input_per_mtok     REAL NOT NULL,
    output_per_mtok    REAL NOT NULL,
    cached_per_mtok    REAL NOT NULL DEFAULT 0,
    chars_per_token    REAL NOT NULL DEFAULT 4.0,
    calibration_factor REAL NOT NULL DEFAULT 1.0,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    session_cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(session)").fetchall()
    }
    if "name" not in session_cols:
        conn.execute(
            "ALTER TABLE session ADD COLUMN name TEXT NOT NULL DEFAULT ''"
        )

    # Session outcome columns (added 2026-05-18)
    for col, ddl in _SESSION_MIGRATIONS:
        if col not in session_cols:
            conn.execute(f"ALTER TABLE session ADD COLUMN {ddl}")

    # Multi-agent foundation columns (added 2026-05-18)
    tc_cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(tool_call)").fetchall()
    }
    for col, ddl in _TOOL_CALL_MIGRATIONS:
        if col not in tc_cols:
            conn.execute(f"ALTER TABLE tool_call ADD COLUMN {ddl}")

    # Backfill project column from session.server_command for old rows
    _backfill_project(conn)

    # Seed default rate card if empty
    count = conn.execute("SELECT COUNT(*) FROM rate_card").fetchone()[0]
    if count == 0:
        _seed_default_rates(conn)


_SESSION_MIGRATIONS = [
    ("commits", "commits INTEGER NOT NULL DEFAULT 0"),
    ("files_changed", "files_changed INTEGER NOT NULL DEFAULT 0"),
    ("tests_passed", "tests_passed INTEGER NOT NULL DEFAULT 0"),
    ("tests_failed", "tests_failed INTEGER NOT NULL DEFAULT 0"),
]

_TOOL_CALL_MIGRATIONS = [
    ("agent", "agent TEXT NOT NULL DEFAULT ''"),
    ("project", "project TEXT NOT NULL DEFAULT ''"),
    ("model_id", "model_id TEXT NOT NULL DEFAULT ''"),
    ("input_size", "input_size INTEGER NOT NULL DEFAULT 0"),
]


def _backfill_project(conn: sqlite3.Connection) -> None:
    """Backfill project column from session.server_command.

    Older hook data recorded server_command (cwd) but didn't set
    the project column. Extract the directory name from the path.
    Only touches rows where project is empty.
    """
    # Quick check — skip if no empty project rows
    count = conn.execute(
        "SELECT COUNT(*) FROM tool_call WHERE project = ''",
    ).fetchone()[0]
    if count == 0:
        return

    rows = conn.execute(
        "SELECT tc.id, s.server_command "
        "FROM tool_call tc "
        "JOIN session s ON s.id = tc.session_id "
        "WHERE tc.project = '' AND s.server_command != ''",
    ).fetchall()

    for r in rows:
        path = r["server_command"].rstrip("/")
        project = path.rsplit("/", 1)[-1] if "/" in path else path
        if project:
            conn.execute(
                "UPDATE tool_call SET project = ? WHERE id = ?",
                (project, r["id"]),
            )


def _seed_default_rates(conn: sqlite3.Connection) -> None:
    """Insert default rate card entries for known models."""
    defaults = [
        # Anthropic
        ("claude-opus-4-6", "Claude Opus 4.6", 15.0, 75.0, 1.5),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6", 3.0, 15.0, 0.3),
        ("claude-haiku-4-5", "Claude Haiku 4.5", 0.8, 4.0, 0.08),
        # Google
        ("gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.0, 0.315),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", 0.15, 0.6, 0.0375),
        # OpenAI
        ("gpt-4.1", "GPT-4.1", 2.0, 8.0, 0.5),
        ("gpt-4.1-mini", "GPT-4.1 Mini", 0.4, 1.6, 0.1),
        ("o3", "o3", 2.0, 8.0, 0.5),
        ("o4-mini", "o4-mini", 1.1, 4.4, 0.275),
    ]
    for model_id, name, inp, out, cached in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO rate_card "
            "(model_id, display_name, input_per_mtok, output_per_mtok, "
            "cached_per_mtok) VALUES (?, ?, ?, ?, ?)",
            (model_id, name, inp, out, cached),
        )
