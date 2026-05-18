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


# ── Export command ─────────────────────────────────────────────────


class TestExportCommand:
    def test_export_jsonl(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        import json
        result = _invoke(cli_runner, ["export"], seeded_db)
        assert result.exit_code == 0
        lines = [x for x in result.output.strip().split("\n") if x]
        assert len(lines) == 4
        # Each line must be valid JSON
        for line in lines:
            obj = json.loads(line)
            assert "tool_name" in obj
            assert "session_id" in obj
            assert "started_at" in obj

    def test_export_filter_by_tool(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        import json
        result = _invoke(cli_runner, ["export", "--tool", "search"], seeded_db)
        assert result.exit_code == 0
        lines = [x for x in result.output.strip().split("\n") if x]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert obj["tool_name"] == "search"

    def test_export_filter_by_session(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        import json
        result = _invoke(
            cli_runner, ["export", "--session", "sess-001"], seeded_db,
        )
        assert result.exit_code == 0
        lines = [x for x in result.output.strip().split("\n") if x]
        assert len(lines) == 4
        for line in lines:
            obj = json.loads(line)
            assert obj["session_id"] == "sess-001"

    def test_export_limit(self, cli_runner: CliRunner, seeded_db: Path) -> None:
        result = _invoke(cli_runner, ["export", "--limit", "2"], seeded_db)
        assert result.exit_code == 0
        lines = [x for x in result.output.strip().split("\n") if x]
        assert len(lines) == 2

    def test_export_empty_db(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["export"], db_path)
        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_export_no_result_json(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        """Export deliberately excludes result_json to keep output size sane."""
        import json
        result = _invoke(cli_runner, ["export", "--limit", "1"], seeded_db)
        obj = json.loads(result.output.strip())
        assert "result_json" not in obj
        assert "result_size" in obj
        assert "agent" in obj
        assert "project" in obj
        assert "model_id" in obj


# ── Strategy command ───────────────────────────────────────────────


@pytest.fixture
def project_db(tmp_path: Path) -> Path:
    """Create a DB with project-tagged tool calls across multiple projects."""
    db_path = tmp_path / "project.db"
    db = MeterDB(db_path)

    session = Session(
        id="sess-proj",
        server_name="claude-code",
        server_command="claude",
        started_at=datetime.now().isoformat(),
    )
    db.create_session(session)

    # Simulate calls across different projects
    projects = [
        ("AgentMeter", 10),
        ("ComplyIT", 3),
        ("MailSift", 5),
        ("Politicks", 8),
    ]
    for project, count in projects:
        for _i in range(count):
            call = ToolCall(
                session_id="sess-proj",
                server_name="claude-code",
                tool_name="Read",
                arguments_json=f'{{"file": "/path/to/{project}/src/main.py"}}',
                result_json="content",
                result_size=100,
                is_error=False,
                started_at=datetime.now().isoformat(),
                elapsed_ms=50,
            )
            db.record_call(call)
            # Tag the project column directly
            db._conn.execute(
                "UPDATE tool_call SET project = ? "
                "WHERE id = (SELECT MAX(id) FROM tool_call)",
                (project,),
            )
            db._conn.commit()

    db.end_session("sess-proj", total_calls=26)
    db.close()
    return db_path


class TestStrategyCommand:
    def test_strategy_shows_projects_and_roles(
        self, cli_runner: CliRunner, project_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["strategy"], project_db)
        assert result.exit_code == 0
        assert "Strategy Report" in result.output
        assert "AgentMeter" in result.output
        assert "ComplyIT" in result.output
        assert "Long-Term Bet" in result.output
        assert "Revenue Engine" in result.output
        assert "Personal" in result.output

    def test_strategy_shows_tool_breakdown(
        self, cli_runner: CliRunner, project_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["strategy"], project_db)
        assert result.exit_code == 0
        assert "tool calls" in result.output
        assert "Top tools" in result.output
        assert "Read" in result.output

    def test_strategy_empty_db(
        self, cli_runner: CliRunner, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "empty.db"
        MeterDB(db_path).close()
        result = _invoke(cli_runner, ["strategy"], db_path)
        assert result.exit_code == 0
        assert "No project-tagged" in result.output

    def test_strategy_backfilled_projects(
        self, cli_runner: CliRunner, seeded_db: Path,
    ) -> None:
        """seeded_db has server_command — backfill extracts project."""
        result = _invoke(cli_runner, ["strategy"], seeded_db)
        assert result.exit_code == 0
        assert "Strategy Report" in result.output

    def test_strategy_custom_days(
        self, cli_runner: CliRunner, project_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["strategy", "--days", "1"], project_db)
        assert result.exit_code == 0
        assert "last 1 days" in result.output

    def test_strategy_shows_all_projects(
        self, cli_runner: CliRunner, project_db: Path,
    ) -> None:
        result = _invoke(cli_runner, ["strategy"], project_db)
        assert result.exit_code == 0
        assert "Politicks" in result.output
        assert "MailSift" in result.output
