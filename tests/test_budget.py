"""Budget enforcement tests for AgentMeter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from agentmeter.db import MeterDB
from agentmeter.models import Budget, Session, ToolCall


@pytest.fixture()
def db(tmp_path: Path) -> MeterDB:
    return MeterDB(tmp_path / "test.db")


@pytest.fixture()
def db_with_session(db: MeterDB) -> tuple[MeterDB, str]:
    """DB with an active session and some tool calls."""
    session_id = "test-session-001"
    db.create_session(Session(
        id=session_id,
        server_name="testserver",
        server_command="python -m test",
        started_at=datetime.now().isoformat(),
    ))
    return db, session_id


def _make_call(
    session_id: str,
    server_name: str = "testserver",
    tool_name: str = "search",
) -> ToolCall:
    return ToolCall(
        session_id=session_id,
        server_name=server_name,
        tool_name=tool_name,
        started_at=datetime.now().isoformat(),
    )


class TestBudgetCRUD:
    """Test budget create/read/update/delete."""

    def test_set_budget(self, db: MeterDB) -> None:
        b = Budget(scope="session", max_calls=50, action="deny")
        row_id = db.set_budget(b)
        assert row_id > 0

        budgets = db.get_budgets()
        assert len(budgets) == 1
        assert budgets[0].scope == "session"
        assert budgets[0].max_calls == 50
        assert budgets[0].action == "deny"

    def test_set_budget_replaces_same_scope(self, db: MeterDB) -> None:
        db.set_budget(Budget(scope="daily", max_calls=100))
        db.set_budget(Budget(scope="daily", max_calls=200))

        budgets = db.get_budgets()
        assert len(budgets) == 1
        assert budgets[0].max_calls == 200

    def test_set_budget_different_servers(self, db: MeterDB) -> None:
        db.set_budget(Budget(
            scope="daily", server_name="", max_calls=100,
        ))
        db.set_budget(Budget(
            scope="daily", server_name="mailsift", max_calls=50,
        ))

        budgets = db.get_budgets()
        assert len(budgets) == 2

    def test_clear_all(self, db: MeterDB) -> None:
        db.set_budget(Budget(scope="session", max_calls=50))
        db.set_budget(Budget(scope="daily", max_calls=200))

        removed = db.clear_budget()
        assert removed == 2
        assert db.get_budgets() == []

    def test_clear_by_scope(self, db: MeterDB) -> None:
        db.set_budget(Budget(scope="session", max_calls=50))
        db.set_budget(Budget(scope="daily", max_calls=200))

        removed = db.clear_budget(scope="session")
        assert removed == 1
        remaining = db.get_budgets()
        assert len(remaining) == 1
        assert remaining[0].scope == "daily"

    def test_clear_by_server(self, db: MeterDB) -> None:
        db.set_budget(Budget(
            scope="daily", server_name="", max_calls=100,
        ))
        db.set_budget(Budget(
            scope="daily", server_name="mailsift", max_calls=50,
        ))

        removed = db.clear_budget(server_name="mailsift")
        assert removed == 1
        remaining = db.get_budgets()
        assert len(remaining) == 1
        assert remaining[0].server_name == ""

    def test_get_budgets_empty(self, db: MeterDB) -> None:
        assert db.get_budgets() == []


class TestBudgetEnforcement:
    """Test budget check logic."""

    def test_no_budgets_allows_calls(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        assert db.check_budget(sid, "testserver") is None

    def test_session_budget_allows_under_limit(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(scope="session", max_calls=5))

        # Record 4 calls — still under limit
        for _ in range(4):
            db.record_call(_make_call(sid))

        assert db.check_budget(sid, "testserver") is None

    def test_session_budget_denies_at_limit(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(scope="session", max_calls=5))

        for _ in range(5):
            db.record_call(_make_call(sid))

        denied = db.check_budget(sid, "testserver")
        assert denied is not None
        assert denied.scope == "session"
        assert denied.max_calls == 5

    def test_session_budget_denies_over_limit(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(scope="session", max_calls=3))

        for _ in range(10):
            db.record_call(_make_call(sid))

        denied = db.check_budget(sid, "testserver")
        assert denied is not None

    def test_daily_budget_denies_at_limit(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(scope="daily", max_calls=10))

        for _ in range(10):
            db.record_call(_make_call(sid))

        denied = db.check_budget(sid, "testserver")
        assert denied is not None
        assert denied.scope == "daily"

    def test_server_specific_budget_ignores_other_servers(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(
            scope="session", server_name="mailsift", max_calls=5,
        ))

        for _ in range(10):
            db.record_call(_make_call(sid, server_name="testserver"))

        # Budget is for mailsift, we're checking testserver
        assert db.check_budget(sid, "testserver") is None

    def test_server_specific_budget_applies_to_matching(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(
            scope="session", server_name="testserver", max_calls=3,
        ))

        for _ in range(3):
            db.record_call(_make_call(sid))

        denied = db.check_budget(sid, "testserver")
        assert denied is not None

    def test_global_budget_applies_to_any_server(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(scope="session", max_calls=3))

        for _ in range(3):
            db.record_call(_make_call(sid))

        denied = db.check_budget(sid, "anyserver")
        assert denied is not None

    def test_warn_budget_does_not_deny(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(
            scope="session", max_calls=3, action="warn",
        ))

        for _ in range(5):
            db.record_call(_make_call(sid))

        # check_budget only returns deny rules
        assert db.check_budget(sid, "testserver") is None

    def test_warn_budget_shows_in_warnings(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(
            scope="session", max_calls=3, action="warn",
        ))

        for _ in range(3):
            db.record_call(_make_call(sid))

        warnings = db.get_budget_warnings(sid, "testserver")
        assert len(warnings) == 1
        assert warnings[0].max_calls == 3

    def test_multiple_budgets_strictest_wins(
        self, db_with_session: tuple[MeterDB, str],
    ) -> None:
        db, sid = db_with_session
        db.set_budget(Budget(scope="session", max_calls=100))
        db.set_budget(Budget(scope="daily", max_calls=5))

        for _ in range(5):
            db.record_call(_make_call(sid))

        denied = db.check_budget(sid, "testserver")
        assert denied is not None
        assert denied.scope == "daily"

    def test_different_sessions_have_independent_counts(
        self, db: MeterDB,
    ) -> None:
        db.set_budget(Budget(scope="session", max_calls=3))

        # Session 1
        db.create_session(Session(
            id="s1", server_name="test",
            server_command="cmd", started_at=datetime.now().isoformat(),
        ))
        for _ in range(3):
            db.record_call(_make_call("s1"))

        # Session 2
        db.create_session(Session(
            id="s2", server_name="test",
            server_command="cmd", started_at=datetime.now().isoformat(),
        ))
        db.record_call(_make_call("s2"))

        # Session 1 is at limit, session 2 is not
        assert db.check_budget("s1", "test") is not None
        assert db.check_budget("s2", "test") is None


class TestBudgetCLI:
    """Test budget CLI commands."""

    def test_budget_set_and_show(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        db_path = tmp_path / "test.db"
        import os
        os.environ["AGENTMETER_DB"] = str(db_path)

        runner = CliRunner()
        result = runner.invoke(main, [
            "budget", "set", "session", "50",
        ])
        assert result.exit_code == 0
        assert "50 calls" in result.output

        result = runner.invoke(main, ["budget", "show"])
        assert result.exit_code == 0
        assert "session" in result.output
        assert "50" in result.output

        del os.environ["AGENTMETER_DB"]

    def test_budget_clear(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        db_path = tmp_path / "test.db"
        import os
        os.environ["AGENTMETER_DB"] = str(db_path)

        runner = CliRunner()
        runner.invoke(main, ["budget", "set", "daily", "100"])
        result = runner.invoke(main, [
            "budget", "clear", "--yes",
        ])
        assert result.exit_code == 0
        assert "Removed 1" in result.output

        del os.environ["AGENTMETER_DB"]

    def test_budget_show_empty(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        db_path = tmp_path / "test.db"
        import os
        os.environ["AGENTMETER_DB"] = str(db_path)

        runner = CliRunner()
        result = runner.invoke(main, ["budget", "show"])
        assert result.exit_code == 0
        assert "No budget rules" in result.output

        del os.environ["AGENTMETER_DB"]
