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
    _normalise_cmd,
    _parse_lint_results,
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


# ── Lint detection ──────────────────────────────────────────


class TestParseLintResults:
    def test_ruff_clean(self) -> None:
        lp, le = _parse_lint_results(
            "ruff check src/", "All checks passed!", False,
        )
        assert lp == 1
        assert le == 0

    def test_ruff_errors(self) -> None:
        lp, le = _parse_lint_results(
            "ruff check src/", "Found 3 errors.", True,
        )
        assert lp == 0
        assert le == 1

    def test_eslint_clean(self) -> None:
        lp, le = _parse_lint_results(
            "eslint .", "no problems found", False,
        )
        assert lp == 1
        assert le == 0

    def test_mypy_error(self) -> None:
        lp, le = _parse_lint_results(
            "mypy src/", "error: Incompatible types", False,
        )
        assert lp == 0
        assert le == 1

    def test_non_lint_command_ignored(self) -> None:
        lp, le = _parse_lint_results(
            "git status", "All checks passed!", False,
        )
        assert lp == 0
        assert le == 0

    def test_lint_no_clear_signal_counts_pass(self) -> None:
        lp, le = _parse_lint_results(
            "ruff check src/", "", False,
        )
        assert lp == 1
        assert le == 0


class TestNormaliseCmd:
    def test_strips_whitespace(self) -> None:
        assert _normalise_cmd("  git  commit  -m 'x'  ") == "git commit -m 'x'"

    def test_identical_commands_match(self) -> None:
        a = _normalise_cmd("python3 -m pytest tests/ -v")
        b = _normalise_cmd("python3  -m  pytest  tests/  -v")
        assert a == b


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
        assert facts["total_calls"] == 3
        assert facts["errors"] == 0
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
        assert facts["total_calls"] == 0
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

    def test_detects_lint(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "lint.db")
        session = Session(
            id="sess-lint",
            server_name="claude-code",
            server_command="/path",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.record_call(ToolCall(
            session_id="sess-lint",
            server_name="claude-code",
            tool_name="Bash",
            arguments_json='{"command": "ruff check src/"}',
            result_json="All checks passed!",
            result_size=20,
            started_at=datetime.now().isoformat(),
            elapsed_ms=200,
        ))
        db.end_session("sess-lint", total_calls=1)
        facts = detect_session_outcome(db, "sess-lint")
        assert facts["lint_passes"] == 1
        assert facts["lint_errors"] == 0
        db.close()

    def test_counts_errors(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "err.db")
        session = Session(
            id="sess-err",
            server_name="claude-code",
            server_command="/path",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.record_call(ToolCall(
            session_id="sess-err",
            server_name="claude-code",
            tool_name="Bash",
            arguments_json='{"command": "cat missing.txt"}',
            result_json="No such file",
            result_size=15,
            is_error=True,
            started_at=datetime.now().isoformat(),
            elapsed_ms=10,
        ))
        db.end_session("sess-err", total_calls=1)
        facts = detect_session_outcome(db, "sess-err")
        assert facts["errors"] == 1
        assert facts["total_calls"] == 1
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


# ── Value multiplier ───────────────────────────────────────


class TestValueMultiplier:
    def test_estimate_dev_minutes(self) -> None:
        from agentmeter.cli_value import estimate_dev_minutes

        minutes = estimate_dev_minutes(
            commits=2, tests_passed=50,
            files_changed=5, lint_passes=3,
        )
        # 2*20 + 50*2 + 5*10 + 3*3 = 40+100+50+9 = 199
        assert minutes == 199

    def test_estimate_zero_outcomes(self) -> None:
        from agentmeter.cli_value import estimate_dev_minutes

        assert estimate_dev_minutes(0, 0, 0, 0) == 0

    def test_estimate_dev_value(self) -> None:
        from agentmeter.cli_value import estimate_dev_value

        # 60 minutes at $150/hr = $150
        assert estimate_dev_value(60, 150) == 150.0
        # 30 minutes at $200/hr = $100
        assert estimate_dev_value(30, 200) == 100.0

    def test_quality_score_perfect(self) -> None:
        from agentmeter.cli_value import quality_score

        score = quality_score(
            errors=0, total_calls=50,
            tests_failed=0, retries=0, lint_errors=0,
        )
        assert score == 100

    def test_quality_score_with_errors(self) -> None:
        from agentmeter.cli_value import quality_score

        score = quality_score(
            errors=10, total_calls=50,
            tests_failed=2, retries=3, lint_errors=1,
        )
        # 100 - 40(err 20%) - 10(2 fails) - 12(3 retries) - 5(1 lint)
        assert score == 33

    def test_quality_score_floors_at_zero(self) -> None:
        from agentmeter.cli_value import quality_score

        score = quality_score(
            errors=50, total_calls=50,
            tests_failed=20, retries=20, lint_errors=10,
        )
        assert score == 0

    def test_value_cli_no_sessions(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["value"],
            env={"AGENTMETER_DB": "/tmp/nonexistent_value_test.db"},
        )
        assert result.exit_code == 0


class TestQualityStorage:
    def test_store_and_retrieve_quality(self, tmp_path: Path) -> None:
        db = MeterDB(tmp_path / "q.db")
        session = Session(
            id="s-q", server_name="test",
            server_command="/p",
            started_at=datetime.now().isoformat(),
        )
        db.create_session(session)
        db.update_session_quality(
            "s-q", lint_passes=3, lint_errors=1,
            retries=2, errors=5, total_calls=50,
        )
        s = db.get_sessions(limit=1)[0]
        assert s.lint_passes == 3
        assert s.lint_errors == 1
        assert s.retries == 2
        assert s.errors == 5
        db.close()
