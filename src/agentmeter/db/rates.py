"""Rate card CRUD operations for AgentMeter."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from agentmeter.models import RateCard


def get_rate(
    conn: sqlite3.Connection, model_id: str,
) -> RateCard | None:
    """Get a single rate card entry by model ID."""
    row = conn.execute(
        "SELECT * FROM rate_card WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_rate(row)


def get_all_rates(conn: sqlite3.Connection) -> list[RateCard]:
    """Get all rate card entries."""
    rows = conn.execute(
        "SELECT * FROM rate_card ORDER BY model_id"
    ).fetchall()
    return [_row_to_rate(r) for r in rows]


def set_rate(conn: sqlite3.Connection, rate: RateCard) -> None:
    """Create or update a rate card entry."""
    conn.execute(
        "INSERT OR REPLACE INTO rate_card "
        "(model_id, display_name, input_per_mtok, output_per_mtok, "
        "cached_per_mtok, chars_per_token, calibration_factor, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rate.model_id,
            rate.display_name,
            rate.input_per_mtok,
            rate.output_per_mtok,
            rate.cached_per_mtok,
            rate.chars_per_token,
            rate.calibration_factor,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def clear_rates(conn: sqlite3.Connection) -> int:
    """Remove all rate card entries. Returns count removed."""
    cursor = conn.execute("DELETE FROM rate_card")
    conn.commit()
    return cursor.rowcount


def _row_to_rate(row: sqlite3.Row) -> RateCard:
    return RateCard(
        model_id=row["model_id"],
        display_name=row["display_name"],
        input_per_mtok=row["input_per_mtok"],
        output_per_mtok=row["output_per_mtok"],
        cached_per_mtok=row["cached_per_mtok"],
        chars_per_token=row["chars_per_token"],
        calibration_factor=row["calibration_factor"],
        updated_at=row["updated_at"],
    )
