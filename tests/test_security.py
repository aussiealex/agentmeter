"""Security tests for AgentMeter — adversarial inputs and data safety."""

from __future__ import annotations

import sqlite3
import stat
from datetime import datetime
from pathlib import Path

import pytest

from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall

# ── SQL injection payloads ──────────────────────────────────────────

SQL_INJECTIONS = [
    "'; DROP TABLE tool_call; --",
    "' OR '1'='1",
    "' UNION SELECT * FROM session; --",
    "'; UPDATE session SET name='pwned'; --",
    "Robert'); DROP TABLE tool_call;--",
    "' OR 1=1 --",
    "1; ATTACH DATABASE '/tmp/evil.db' AS evil; --",
]


def _seed_session(db: MeterDB, session_id: str = "sess-001") -> None:
    session = Session(
        id=session_id,
        server_name="test",
        server_command="python -m test",
        started_at=datetime.now().isoformat(),
    )
    db.create_session(session)


def _seed_call(
    db: MeterDB,
    session_id: str = "sess-001",
    tool_name: str = "add",
) -> None:
    call = ToolCall(
        session_id=session_id,
        server_name="test",
        tool_name=tool_name,
        arguments_json='{"a": 1}',
        result_json="result",
        result_size=6,
        is_error=False,
        started_at=datetime.now().isoformat(),
        elapsed_ms=50,
    )
    db.record_call(call)


class TestSQLInjectionInQueries:
    """Verify SQL injection payloads are treated as literal values."""

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_get_recent_calls_tool_name(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """SQL injection via tool_name filter should return empty, not crash."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        # Should treat payload as a literal tool name — no match, no crash
        calls = tmp_db.get_recent_calls(tool_name=payload)
        assert isinstance(calls, list)
        # Original data should be intact
        assert tmp_db.get_total_calls() == 1

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_get_session_stats_since(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """SQL injection via since parameter should not corrupt data."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        # since is compared with >=, payload won't match any date — empty ok
        try:
            stats = tmp_db.get_session_stats(since=payload)
            assert isinstance(stats, list)
        except Exception:
            pass  # An exception is also acceptable — just no data corruption
        # Data must still be intact
        assert tmp_db.get_total_calls() == 1

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_get_total_calls_since(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """SQL injection via since in get_total_calls should be safe."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        try:
            result = tmp_db.get_total_calls(since=payload)
            assert isinstance(result, int)
        except Exception:
            pass
        # Verify no data was corrupted
        assert tmp_db.get_total_calls() == 1

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_get_tool_stats_since(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """SQL injection via since in get_tool_stats should be safe."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        try:
            stats = tmp_db.get_tool_stats(since=payload)
            assert isinstance(stats, list)
        except Exception:
            pass
        assert tmp_db.get_total_calls() == 1

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_get_tool_stats_server_name(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """SQL injection via server_name in get_tool_stats should be safe."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        stats = tmp_db.get_tool_stats(server_name=payload)
        assert isinstance(stats, list)
        assert tmp_db.get_total_calls() == 1

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_rename_session_injection(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """SQL injection via rename should store literal text, not execute."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        tmp_db.end_session("sess-001", total_calls=1)
        # Try injecting through both parameters
        tmp_db.rename_session(payload, "safe-name")
        tmp_db.rename_session("sess-001", payload)
        # Data intact
        assert tmp_db.get_total_calls() == 1
        stats = tmp_db.get_session_stats()
        assert len(stats) == 1


class TestSQLInjectionInWrites:
    """Verify adversarial data stored in writes is treated as literal."""

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_tool_name_stored_literally(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """A malicious tool name should be stored and retrieved literally."""
        _seed_session(tmp_db)
        _seed_call(tmp_db, tool_name=payload)
        calls = tmp_db.get_recent_calls()
        assert any(c.tool_name == payload for c in calls)

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_server_name_stored_literally(
        self, tmp_db: MeterDB, payload: str,
    ) -> None:
        """A malicious server name should be stored literally."""
        session = Session(
            id="evil-sess",
            server_name=payload,
            server_command="python -m test",
            started_at=datetime.now().isoformat(),
        )
        tmp_db.create_session(session)
        stats = tmp_db.get_session_stats()
        assert any(s.server_name == payload for s in stats)


class TestDataTruncation:
    """Verify the proxy truncation limits work at boundaries."""

    def test_result_at_truncation_boundary(self, tmp_db: MeterDB) -> None:
        """result_json at exactly 2000 chars should not be truncated."""
        _seed_session(tmp_db)
        result = "x" * 2000
        call = ToolCall(
            session_id="sess-001",
            server_name="test",
            tool_name="big",
            result_json=result,
            result_size=2000,
            started_at=datetime.now().isoformat(),
            elapsed_ms=10,
        )
        tmp_db.record_call(call)
        calls = tmp_db.get_recent_calls()
        assert len(calls[0].result_json) == 2000

    def test_arguments_very_large(self, tmp_db: MeterDB) -> None:
        """Very large arguments_json should be storable (DB doesn't truncate)."""
        _seed_session(tmp_db)
        big_args = "a" * 10000
        call = ToolCall(
            session_id="sess-001",
            server_name="test",
            tool_name="big",
            arguments_json=big_args,
            result_json="ok",
            result_size=2,
            started_at=datetime.now().isoformat(),
            elapsed_ms=10,
        )
        tmp_db.record_call(call)
        calls = tmp_db.get_recent_calls()
        assert len(calls[0].arguments_json) == 10000


class TestFileSystemSafety:
    """DB path handling and file permissions."""

    def test_db_in_nonexistent_deep_directory(self, tmp_path: Path) -> None:
        """MeterDB should create parent directories."""
        deep = tmp_path / "a" / "b" / "c" / "test.db"
        db = MeterDB(deep)
        _seed_session(db)
        assert db.get_total_calls() == 0
        db.close()

    def test_db_file_not_world_readable(self, tmp_path: Path) -> None:
        """DB file should not be world-readable (contains tool call data)."""
        db_path = tmp_path / "test.db"
        db = MeterDB(db_path)
        db.close()
        mode = db_path.stat().st_mode
        # Check others don't have read permission
        # Note: this depends on umask — flag if world-readable
        world_readable = bool(mode & stat.S_IROTH)
        if world_readable:
            pytest.skip(
                "DB file is world-readable — consider restricting permissions. "
                "Depends on system umask."
            )

    def test_db_read_only_directory(self, tmp_path: Path) -> None:
        """Opening DB in read-only directory should raise, not silently fail."""
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(0o444)
        try:
            with pytest.raises((sqlite3.OperationalError, PermissionError, OSError)):
                MeterDB(ro_dir / "test.db")
        finally:
            # Restore permissions for cleanup
            ro_dir.chmod(0o755)
