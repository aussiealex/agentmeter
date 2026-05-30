"""Tests for session outcome detection."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentmeter.cli import main
from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall
from agentmeter.outcomes import (
    _count_commits,
    _count_files_changed,
    _parse_test_results,
    detect_session_outcome,
)

# ── Parsing helpers ──────────────────────────────────────────


class TestCountCommits:
    def test_detects_commit(self) -> None:
        output = "[main abc1234] feat: add export command"
        assert _count_commits(output) == 1

    def test_detects_multiple_commits(self) -> None:
        output = (
            "[main abc1234] first commit\n"
            "[main def5678] second commit"
        )
        assert _count_commits(output) == 2

    def test_no_commits(self) -> None:
        assert _count_commits("nothing here") == 0

    def test_branch_with_slash(self) -> None:
        output = "[feat/export abc1234] add export"
        assert _count_commits(output) == 1


class TestCountFilesChanged:
    def test_single_file(self) -> None:
        output = "1 file changed, 10 insertions(+)"
        assert _count_files_changed(output) == 1

    def test_multiple_files(self) -> None:
        output = "5 files changed, 100 insertions(+), 20 deletions(-)"
        assert _count_files_changed(output) == 5

    def test_no_files(self) -> None:
        assert _count_files_changed("nothing") == 0

    def test_multiple_diffs(self) -> None:
        output = (
            "3 files changed, 10 insertions(+)\n"
            "2 files changed, 5 deletions(-)"
        )
        assert _count_files_changed(output) == 5


class TestParseTestResults:
    def test_all_passed(self) -> None:
        output = "======= 245 passed in 12.01s ======="
        passed, failed = _parse_test_results(output)
        assert passed == 245
        assert failed == 0

    def test_some_failed(self) -> None:
        output = "======= 240 passed, 5 failed in 12.01s ======="
        passed, failed = _parse_test_results(output)
        assert passed == 240
        assert failed == 5

    def test_only_failed(self) -> None:
        output = "======= 3 failed in 0.5s ======="
        passed, failed = _parse_test_results(output)
        assert passed == 0
        assert failed == 3

    def test_no_test_output(self) -> None:
        passed, failed = _parse_test_results("ls -la")
        assert passed == 0
        assert failed == 0

    def test_takes_max_across_lines(self) -> None:
        """Multiple pytest runs — take the highest count."""
        output = (
            "10 passed in 1s\n"
            "245 passed in 12s"
        )
        passed, failed = _parse_test_results(output)
        assert passed == 245


# ── Integration: detect_session_outcome ──────────────────────


@pytest.fixture
def outcome_db(tmp_path: Path) -> tuple[MeterDB, str]:
    """DB with a session containing git + test Bash calls."""
    db_path = tmp_path / "outcome.db"
    db = MeterDB(db_path)

    session = Session(
        id="sess-out",
        server_name="claude-code",
        server_command="/path/to/MyProject",
        started_at=datetime.now().isoformat(),
    )
    db.create_session(session)

    # A git commit call
    db.record_call(ToolCall(
        session_id="sess-out",
        server_name="claude-code",
        tool_name="Bash",
        arguments_json='{"command": "git commit -m \\"fix bug\\""}',
        result_json=(
            '[main a1b2c3d] fix bug\n'
            ' 3 files changed, 45 insertions(+), 12 deletions(-)'
        ),
        result_size=80,
        started_at=datetime.now().isoformat(),
        elapsed_ms=500,
    ))

    # A pytest call
    db.record_call(ToolCall(
        session_id="sess-out",
        server_name="claude-code",
        tool_name="Bash",
        arguments_json='{"command": "python3 -m pytest tests/ -v"}',
        result_json="257 passed, 1 skipped in 12.01s",
        result_size=40,
        started_at=datetime.now().isoformat(),
        elapsed_ms=12000,
    ))

    # A non-Bash call (should be ignored)
    db.record_call(ToolCall(
        session_id="sess-out",
        server_name="claude-code",
        tool_name="Read",
        arguments_json='{"file": "src/main.py"}',
        result_json="file content",
        result_size=100,
        started_at=datetime.now().isoformat(),
        elapsed_ms=10,
    ))

    db.end_session("sess-out", total_calls=3)
    return db, "sess-out"


class TestDetectSessionOutcome:
    def test_detects_commits_and_tests(
        self, outcome_db: tuple[MeterDB, str],
    ) -> None:
        db, session_id = outcome_db
        facts = detect_session_outcome(db, session_id)
        assert facts["commits"] == 1
        assert facts["files_changed"] == 3
        assert facts["tests_passed"] == 257
        assert facts["tests_failed"] == 0
        db.close()

    def test_empty_session(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "empty.db")
        session = Session(
            id="sess-empty",
            server_name="claude-code",
            server_command="/path",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.end_session("sess-empty", total_calls=0)
        facts = detect_session_outcome(db, "sess-empty")
        assert facts["commits"] == 0
        assert facts["tests_passed"] == 0
        db.close()

    def test_failed_tests(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "fail.db")
        session = Session(
            id="sess-fail",
            server_name="claude-code",
            server_command="/path",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.record_call(ToolCall(
            session_id="sess-fail",
            server_name="claude-code",
            tool_name="Bash",
            arguments_json='{"command": "pytest"}',
            result_json="240 passed, 5 failed in 10s",
            result_size=30,
            started_at=datetime.now().isoformat(),
            elapsed_ms=10000,
        ))
        db.end_session("sess-fail", total_calls=1)
        facts = detect_session_outcome(db, "sess-fail")
        assert facts["tests_passed"] == 240
        assert facts["tests_failed"] == 5
        db.close()


# ── Store and retrieve outcomes ──────────────────────────────


class TestOutcomeStorage:
    def test_store_and_derive_outcome(
        self, outcome_db: tuple[MeterDB, str],
    ) -> None:
        db, session_id = outcome_db
        db.update_session_outcome(
            session_id, commits=2, files_changed=5,
            tests_passed=100, tests_failed=0,
        )
        sessions = db.get_sessions(limit=1)
        s = sessions[0]
        assert s.commits == 2
        assert s.files_changed == 5
        assert s.tests_passed == 100
        assert s.outcome == "tested+committed"
        db.close()

    def test_outcome_failed(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "fail.db")
        session = Session(
            id="s1", server_name="test",
            server_command="/p",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.update_session_outcome(
            "s1", commits=1, files_changed=3,
            tests_passed=200, tests_failed=5,
        )
        s = db.get_sessions(limit=1)[0]
        assert s.outcome == "failed"
        db.close()

    def test_outcome_committed_only(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "c.db")
        session = Session(
            id="s1", server_name="test",
            server_command="/p",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.update_session_outcome(
            "s1", commits=3, files_changed=10,
            tests_passed=0, tests_failed=0,
        )
        s = db.get_sessions(limit=1)[0]
        assert s.outcome == "committed"
        db.close()

    def test_outcome_empty(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "e.db")
        session = Session(
            id="s1", server_name="test",
            server_command="/p",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        s = db.get_sessions(limit=1)[0]
        assert s.outcome == ""
        db.close()


# ── Backfill CLI command ─────────────────────────────────────


class TestBackfillCommand:
    def test_backfill(
        self, outcome_db: tuple[MeterDB, str],
    ) -> None:
        db, session_id = outcome_db
        db.close()

        runner = CliRunner()
        db_path = outcome_db[0]._path
        result = runner.invoke(
            main, ["backfill"],
            env={"AGENTMETER_DB": str(db_path)},
        )
        assert result.exit_code == 0
        assert "Updated" in result.output

        # Verify outcomes were written
        db2 = MeterDB(db_path)
        s = db2.get_sessions(limit=1)[0]
        assert s.commits == 1
        assert s.tests_passed == 257
        assert s.outcome == "tested+committed"
        db2.close()
