"""SQLite storage for AgentMeter metering data.

MeterDB is the single entry point — one connection, one class.
Submodules handle domain-specific logic, taking conn as first arg.

Usage:
    from agentmeter.db import MeterDB
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from agentmeter.db import analytics, breaker, budget, calls, rates, sessions
from agentmeter.db.schema import init_schema
from agentmeter.models import (
    BreakerConfig,
    BreakerTrip,
    Budget,
    DailyTotal,
    ProjectStats,
    RateCard,
    ServerDistribution,
    Session,
    SessionStats,
    ToolCall,
    ToolStats,
)
from agentmeter.platform import data_dir


def _default_db_path() -> Path:
    """Get DB path from env or use default."""
    env_path = os.environ.get("AGENTMETER_DB")
    if env_path:
        return Path(env_path)
    return data_dir() / "agentmeter.db"


class MeterDB:
    """SQLite database for metering records."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        init_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    # ── Session operations ──────────────────────────────────────────

    def create_session(self, session: Session) -> None:
        sessions.create_session(self._conn, session)

    def ensure_session(self, session: Session) -> None:
        sessions.ensure_session(self._conn, session)

    def end_session(self, session_id: str, total_calls: int) -> None:
        sessions.end_session(self._conn, session_id, total_calls)

    def rename_session(self, session_id: str, name: str) -> bool:
        return sessions.rename_session(self._conn, session_id, name)

    def get_sessions(self, limit: int = 20) -> list[Session]:
        return sessions.get_sessions(self._conn, limit)

    def update_session_outcome(
        self,
        session_id: str,
        commits: int,
        files_changed: int,
        tests_passed: int,
        tests_failed: int,
    ) -> bool:
        return sessions.update_session_outcome(
            self._conn, session_id,
            commits, files_changed, tests_passed, tests_failed,
        )

    # ── Tool call operations ────────────────────────────────────────

    def record_call(self, call: ToolCall) -> None:
        calls.record_call(self._conn, call)

    def get_tool_stats(
        self,
        since: str | None = None,
        server_name: str | None = None,
        project: str | None = None,
    ) -> list[ToolStats]:
        return calls.get_tool_stats(
            self._conn, since, server_name, project,
        )

    def get_recent_calls(
        self,
        limit: int = 50,
        tool_name: str | None = None,
        project: str | None = None,
        since: str | None = None,
    ) -> list[ToolCall]:
        return calls.get_recent_calls(
            self._conn, limit, tool_name, project, since,
        )

    def get_total_calls(self, since: str | None = None) -> int:
        return calls.get_total_calls(self._conn, since)

    def get_calls_for_export(
        self,
        since: str | None = None,
        tool_name: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        project: str | None = None,
    ) -> list[ToolCall]:
        return calls.get_calls_for_export(
            self._conn, since, tool_name, session_id, limit,
            project,
        )

    # ── Budget operations ───────────────────────────────────────────

    def set_budget(self, b: Budget) -> int:
        return budget.set_budget(self._conn, b)

    def get_budgets(self) -> list[Budget]:
        return budget.get_budgets(self._conn)

    def clear_budget(
        self,
        scope: str | None = None,
        server_name: str | None = None,
    ) -> int:
        return budget.clear_budget(self._conn, scope, server_name)

    def check_budget(
        self,
        session_id: str,
        server_name: str,
    ) -> Budget | None:
        return budget.check_budget(self._conn, session_id, server_name)

    def get_budget_warnings(
        self,
        session_id: str,
        server_name: str,
    ) -> list[Budget]:
        return budget.get_budget_warnings(self._conn, session_id, server_name)

    # ── Circuit breaker operations ──────────────────────────────────

    def set_breaker(self, config: BreakerConfig) -> int:
        return breaker.set_breaker(self._conn, config)

    def get_breakers(self) -> list[BreakerConfig]:
        return breaker.get_breakers(self._conn)

    def get_breaker_for_server(
        self, server_name: str,
    ) -> BreakerConfig | None:
        return breaker.get_breaker_for_server(self._conn, server_name)

    def clear_breakers(
        self, server_name: str | None = None,
    ) -> int:
        return breaker.clear_breakers(self._conn, server_name)

    def record_breaker_trip(
        self,
        server_name: str,
        call_count: int,
        window_seconds: int,
    ) -> None:
        breaker.record_breaker_trip(
            self._conn, server_name, call_count, window_seconds,
        )

    def get_breaker_trips(self, limit: int = 10) -> list[BreakerTrip]:
        return breaker.get_breaker_trips(self._conn, limit)

    # ── Analytics operations ────────────────────────────────────────

    def get_session_stats(
        self,
        since: str | None = None,
        limit: int = 20,
    ) -> list[SessionStats]:
        return analytics.get_session_stats(self._conn, since, limit)

    def get_daily_totals(
        self, days: int = 7, project: str | None = None,
    ) -> list[DailyTotal]:
        return analytics.get_daily_totals(self._conn, days, project)

    def get_session_distribution(
        self,
        server_name: str | None = None,
    ) -> list[ServerDistribution]:
        return analytics.get_session_distribution(self._conn, server_name)

    # ── Project analytics ──────────────────────────────────────────────

    def get_project_stats(
        self, since: str | None = None,
    ) -> list[ProjectStats]:
        return analytics.get_project_stats(self._conn, since)

    def get_project_tool_breakdown(
        self, project: str, since: str | None = None,
    ) -> list[ToolStats]:
        return analytics.get_project_tool_breakdown(
            self._conn, project, since,
        )

    # ── Rate card operations ─────────────────────────────────────────

    def get_rate(self, model_id: str) -> RateCard | None:
        return rates.get_rate(self._conn, model_id)

    def get_all_rates(self) -> list[RateCard]:
        return rates.get_all_rates(self._conn)

    def set_rate(self, rate: RateCard) -> None:
        rates.set_rate(self._conn, rate)

    def clear_rates(self) -> int:
        return rates.clear_rates(self._conn)
