"""Tests for the PostToolUse hook (agentmeter.hook)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentmeter.db import MeterDB


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AGENTMETER_DB", str(db_path))
    return db_path


def _run_hook(
    payload: dict,
    env_override: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess, feeding JSON on stdin."""
    import os

    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "agentmeter.hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )


class TestHookBasic:
    """Core hook functionality."""

    def test_records_builtin_tool(self, tmp_db: Path) -> None:
        payload = {
            "session_id": "sess-1",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/test.py"},
            "tool_response": "file contents here",
        }
        result = _run_hook(payload)
        assert result.returncode == 0

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].tool_name == "Read"
        assert calls[0].server_name == "claude-code"
        assert calls[0].session_id == "sess-1"
        assert "/tmp/test.py" in calls[0].arguments_json
        db.close()

    def test_creates_session(self, tmp_db: Path) -> None:
        payload = {
            "session_id": "sess-2",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/x.py",
                "old_string": "a",
                "new_string": "b",
            },
            "tool_response": "The file has been updated successfully.",
        }
        _run_hook(payload)

        db = MeterDB(tmp_db)
        sessions = db.get_sessions(limit=10)
        assert len(sessions) == 1
        assert sessions[0].id == "sess-2"
        assert sessions[0].server_name == "claude-code"
        db.close()

    def test_multiple_calls_same_session(self, tmp_db: Path) -> None:
        for tool in ["Read", "Edit", "Bash"]:
            _run_hook({
                "session_id": "sess-3",
                "tool_name": tool,
                "tool_input": {"cmd": f"do {tool}"},
                "tool_response": "ok",
            })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 3
        sessions = db.get_sessions(limit=10)
        assert len(sessions) == 1  # single session, not 3
        db.close()


class TestHookFiltering:
    """MCP tool filtering to prevent double-counting."""

    def test_skips_mcp_tools(self, tmp_db: Path) -> None:
        payload = {
            "session_id": "sess-4",
            "tool_name": "mcp__mailsift__search",
            "tool_input": {"query": "test"},
            "tool_response": "results",
        }
        _run_hook(payload)

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 0
        db.close()

    def test_skips_mcp_various_prefixes(self, tmp_db: Path) -> None:
        for tool in ["mcp__agentmeter__stats", "mcp__github__pr", "mcp__slack__send"]:
            _run_hook({
                "session_id": "sess-5",
                "tool_name": tool,
                "tool_input": {},
                "tool_response": "ok",
            })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 0
        db.close()

    def test_records_non_mcp_tools(self, tmp_db: Path) -> None:
        for tool in ["Read", "Edit", "Bash", "Glob", "Grep", "Write", "WebFetch"]:
            _run_hook({
                "session_id": "sess-6",
                "tool_name": tool,
                "tool_input": {},
                "tool_response": "ok",
            })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 7
        db.close()


class TestHookEdgeCases:
    """Edge cases and robustness."""

    def test_empty_stdin(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "agentmeter.hook"],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0

    def test_invalid_json(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "agentmeter.hook"],
            input="not json at all",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0  # graceful exit, no crash

    def test_missing_fields(self, tmp_db: Path) -> None:
        result = _run_hook({"tool_name": "Read"})
        assert result.returncode == 0

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].session_id == "unknown"
        db.close()

    def test_truncates_large_args(self, tmp_db: Path) -> None:
        big_input = {"data": "x" * 5000}
        _run_hook({
            "session_id": "sess-7",
            "tool_name": "Write",
            "tool_input": big_input,
            "tool_response": "ok",
        })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert len(calls[0].arguments_json) <= 1000
        db.close()

    def test_truncates_large_response(self, tmp_db: Path) -> None:
        _run_hook({
            "session_id": "sess-8",
            "tool_name": "Read",
            "tool_input": {},
            "tool_response": "y" * 10000,
        })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert len(calls[0].result_json) <= 2000
        db.close()

    def test_dict_response(self, tmp_db: Path) -> None:
        _run_hook({
            "session_id": "sess-9",
            "tool_name": "Glob",
            "tool_input": {"pattern": "*.py"},
            "tool_response": {"files": ["a.py", "b.py"]},
        })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert "a.py" in calls[0].result_json
        db.close()


class TestHookWorkingDirectory:
    """Working directory capture for per-project analytics."""

    def test_captures_cwd_as_server_command(self, tmp_db: Path) -> None:
        _run_hook({
            "session_id": "sess-10",
            "tool_name": "Read",
            "tool_input": {},
            "tool_response": "ok",
        })

        db = MeterDB(tmp_db)
        sessions = db.get_sessions(limit=10)
        assert len(sessions) == 1
        # server_command stores the working directory
        assert sessions[0].server_command != ""
        db.close()


class TestEnsureSession:
    """Test the ensure_session DB method directly."""

    def test_idempotent(self, tmp_db: Path) -> None:
        from agentmeter.models import Session

        db = MeterDB(tmp_db)
        session = Session(
            id="idem-1",
            server_name="claude-code",
            server_command="/tmp",
            started_at="2026-01-01T00:00:00",
        )
        db.ensure_session(session)
        db.ensure_session(session)  # should not raise

        sessions = db.get_sessions(limit=10)
        assert len(sessions) == 1
        db.close()

    def test_does_not_overwrite_existing(self, tmp_db: Path) -> None:
        from agentmeter.models import Session

        db = MeterDB(tmp_db)
        db.ensure_session(Session(
            id="idem-2",
            server_name="claude-code",
            server_command="/first/path",
            started_at="2026-01-01T00:00:00",
        ))
        db.ensure_session(Session(
            id="idem-2",
            server_name="claude-code",
            server_command="/second/path",
            started_at="2026-01-02T00:00:00",
        ))

        sessions = db.get_sessions(limit=10)
        assert len(sessions) == 1
        assert sessions[0].server_command == "/first/path"
        db.close()
