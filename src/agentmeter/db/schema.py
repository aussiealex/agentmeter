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
    cache_write_per_mtok REAL NOT NULL DEFAULT 0,
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

    # Rate card migrations
    rc_cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(rate_card)").fetchall()
    }
    if "cache_write_per_mtok" not in rc_cols:
        conn.execute(
            "ALTER TABLE rate_card "
            "ADD COLUMN cache_write_per_mtok REAL NOT NULL DEFAULT 0"
        )

    # Backfill project column from session.server_command for old rows
    _backfill_project(conn)

    # Seed default rate card if empty
    count = conn.execute("SELECT COUNT(*) FROM rate_card").fetchone()[0]
    if count == 0:
        _seed_default_rates(conn)

    # Backfill cache_write_per_mtok for existing rate cards
    _backfill_cache_write_rates(conn)


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
    # (model_id, name, input, output, cache_read, cache_write)
    defaults = [
        # Anthropic — cache write 1.25x input, cache read 0.10x input
        ("claude-opus-4-6", "Claude Opus 4.6", 15.0, 75.0, 1.5, 18.75),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6", 3.0, 15.0, 0.3, 3.75),
        ("claude-haiku-4-5", "Claude Haiku 4.5", 0.8, 4.0, 0.08, 1.0),
        # Google — no cache write premium (same as input)
        ("gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.0, 0.315, 1.25),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", 0.15, 0.6, 0.0375, 0.15),
        # OpenAI — no cache write premium (same as input)
        ("gpt-4.1", "GPT-4.1", 2.0, 8.0, 0.5, 2.0),
        ("gpt-4.1-mini", "GPT-4.1 Mini", 0.4, 1.6, 0.1, 0.4),
        ("o3", "o3", 2.0, 8.0, 0.5, 2.0),
        ("o4-mini", "o4-mini", 1.1, 4.4, 0.275, 1.1),
    ]
    for model_id, name, inp, out, cached, cache_write in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO rate_card "
            "(model_id, display_name, input_per_mtok, output_per_mtok, "
            "cached_per_mtok, cache_write_per_mtok) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (model_id, name, inp, out, cached, cache_write),
        )


def _backfill_cache_write_rates(conn: sqlite3.Connection) -> None:
    """Set cache_write_per_mtok for existing rate cards where it's 0."""
    rows = conn.execute(
        "SELECT model_id, input_per_mtok FROM rate_card "
        "WHERE cache_write_per_mtok = 0 OR cache_write_per_mtok IS NULL",
    ).fetchall()
    for r in rows:
        model_id = r["model_id"]
        input_rate = r["input_per_mtok"]
        # Anthropic models get 1.25x write premium, others get 1.0x
        write_rate = input_rate * 1.25 if model_id.startswith("claude-") else input_rate
        conn.execute(
            "UPDATE rate_card SET cache_write_per_mtok = ? WHERE model_id = ?",
            (write_rate, model_id),
        )
