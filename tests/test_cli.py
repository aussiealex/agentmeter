"""CLI tests for AgentMeter — user-facing command behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentmeter.cli import main
from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """Create a DB with some test data, return path."""
    db_path = tmp_path / "test.db"
    db = MeterDB(db_path)

    session = Session(
        id="sess-001",
        server_name="testserver",
        server_command="python -m test",
        started_at=datetime.now().isoformat(),
    )
    db.create_session(session)

    for tool in ["search", "search", "fetch", "parse"]:
        call = ToolCall(
            session_id="sess-001",
            server_name="testserver",
            tool_name=tool,
            arguments_json='{"q": "test"}',
            result_json="some result",
            result_size=11,
            is_error=tool == "parse",
            started_at=datetime.now().isoformat(),
            elapsed_ms=100,
        )
        db.record_call(call)

    db.end_session("sess-001", total_calls=4)
    db.close()
    return db_path


def _invoke(cli_runner: CliRunner, args: list[str], db_path: Path) -> object:
    """Invoke CLI with AGENTMETER_DB pointing to test DB."""
    env = {"AGENTMETER_DB": str(db_path)}
    return cli_runner.invoke(main, args, env=env)


# ── Stats command ───────────────────────────────────────────────────


class TestStatsCommand:
    def test_stats_default(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["stats"], seeded_db)
        assert result.exit_code == 0
        assert "AgentMeter Stats" in result.output
        assert "4 calls" in result.output

    def test_stats_all(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["stats", "--all"], seeded_db)
        assert result.exit_code == 0
        assert "4 calls" in result.output

    def test_stats_week(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["stats", "--week"], seeded_db)
        assert result.exit_code == 0

    def test_stats_empty_db(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["stats"], db_path)
        assert result.exit_code == 0
        assert "No tool calls" in result.output

    def test_stats_with_server_filter(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["stats", "--server", "testserver"], seeded_db)
        assert result.exit_code == 0
        assert "4 calls" in result.output

    def test_stats_with_nonexistent_server(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["stats", "--server", "nope"], seeded_db)
        assert result.exit_code == 0
        assert "No tool calls" in result.output

    def test_stats_distribution(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["stats", "--distribution"], seeded_db)
        assert result.exit_code == 0
        assert "Session Distribution" in result.output
        assert "testserver" in result.output
        assert "p50" in result.output
        assert "p90" in result.output
        assert "p99" in result.output

    def test_stats_distribution_with_server_filter(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(
            cli_runner, ["stats", "--distribution", "--server", "testserver"],
            seeded_db,
        )
        assert result.exit_code == 0
        assert "testserver" in result.output

    def test_stats_distribution_empty_db(
        self, cli_runner: CliRunner, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["stats", "--distribution"], db_path)
        assert result.exit_code == 0
        assert "No sessions" in result.output


# ── Calls command ───────────────────────────────────────────────────


class TestCallsCommand:
    def test_calls_default(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["calls"], seeded_db)
        assert result.exit_code == 0
        assert "search" in result.output
        assert "fetch" in result.output

    def test_calls_filter_by_tool(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["calls", "--tool", "search"], seeded_db)
        assert result.exit_code == 0
        assert "search" in result.output
        # fetch should NOT appear when filtering
        assert "fetch" not in result.output

    def test_calls_limit(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["calls", "--limit", "1"], seeded_db)
        assert result.exit_code == 0

    def test_calls_empty_db(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["calls"], db_path)
        assert result.exit_code == 0
        assert "No tool calls" in result.output

    def test_calls_nonexistent_tool(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["calls", "--tool", "nonexistent"], seeded_db)
        assert result.exit_code == 0
        assert "No tool calls" in result.output


# ── Sessions command ────────────────────────────────────────────────


class TestSessionsCommand:
    def test_sessions_default(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["sessions"], seeded_db)
        assert result.exit_code == 0
        assert "testserver" in result.output
        assert "4 calls" in result.output

    def test_sessions_empty_db(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["sessions"], db_path)
        assert result.exit_code == 0
        assert "No sessions" in result.output

    def test_sessions_limit(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["sessions", "--limit", "1"], seeded_db)
        assert result.exit_code == 0


# ── Daily command ───────────────────────────────────────────────────


class TestDailyCommand:
    def test_daily_default(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["daily"], seeded_db)
        assert result.exit_code == 0
        assert "Daily Totals" in result.output

    def test_daily_custom_days(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["daily", "--days", "1"], seeded_db)
        assert result.exit_code == 0

    def test_daily_empty_db(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["daily"], db_path)
        assert result.exit_code == 0
        assert "No data" in result.output


# ── Rename command ──────────────────────────────────────────────────


class TestRenameCommand:
    def test_rename_success(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(
            cli_runner, ["rename", "sess-001", "my session"], seeded_db,
        )
        assert result.exit_code == 0
        assert "Renamed to" in result.output

    def test_rename_nonexistent(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(
            cli_runner, ["rename", "no-such-id", "name"], seeded_db,
        )
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_rename_missing_args(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["rename"], seeded_db)
        assert result.exit_code != 0


# ── Formatting edge cases ──────────────────────────────────────────


class TestFormattingEdgeCases:
    def test_error_shown_in_calls(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["calls"], seeded_db)
        assert "ERR" in result.output
        assert "OK" in result.output

    def test_error_count_in_stats(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["stats", "--all"], seeded_db)
        assert "1 errors" in result.output

    def test_bar_chart_in_daily(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["daily"], seeded_db)
        assert "█" in result.output
