"""Unit tests for MeterDB."""

from __future__ import annotations

from datetime import datetime

from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall


def _make_session(
    db: MeterDB, session_id: str = "sess-001", server_name: str = "test",
) -> Session:
    session = Session(
        id=session_id,
        server_name=server_name,
        server_command="python -m test",
        started_at=datetime.now().isoformat(),
    )
    db.create_session(session)
    return session


def _make_call(
    db: MeterDB,
    session_id: str = "sess-001",
    tool_name: str = "add",
    is_error: bool = False,
    elapsed_ms: int = 50,
    result_size: int = 10,
) -> ToolCall:
    call = ToolCall(
        session_id=session_id,
        server_name="test",
        tool_name=tool_name,
        arguments_json='{"a": 1}',
        result_json="result",
        result_size=result_size,
        is_error=is_error,
        started_at=datetime.now().isoformat(),
        elapsed_ms=elapsed_ms,
    )
    db.record_call(call)
    return call


class TestSessionOperations:
    def test_create_session(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        stats = tmp_db.get_session_stats()
        assert len(stats) == 1
        assert stats[0].session_id == "sess-001"

    def test_end_session(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        tmp_db.end_session("sess-001", total_calls=5)
        stats = tmp_db.get_session_stats()
        assert stats[0].total_calls == 5


class TestRecordCalls:
    def test_record_and_retrieve(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="add")
        _make_call(tmp_db, tool_name="echo")

        calls = tmp_db.get_recent_calls()
        assert len(calls) == 2
        tool_names = {c.tool_name for c in calls}
        assert tool_names == {"add", "echo"}

    def test_total_calls(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        for _ in range(5):
            _make_call(tmp_db)
        assert tmp_db.get_total_calls() == 5

    def test_filter_by_tool_name(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="add")
        _make_call(tmp_db, tool_name="add")
        _make_call(tmp_db, tool_name="echo")

        calls = tmp_db.get_recent_calls(tool_name="add")
        assert len(calls) == 2
        assert all(c.tool_name == "add" for c in calls)


class TestErrorTracking:
    def test_error_recorded(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="fail", is_error=True)

        calls = tmp_db.get_recent_calls(tool_name="fail")
        assert len(calls) == 1
        assert calls[0].is_error is True

    def test_error_count_in_stats(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="add", is_error=False)
        _make_call(tmp_db, tool_name="add", is_error=False)
        _make_call(tmp_db, tool_name="add", is_error=True)

        stats = tmp_db.get_tool_stats()
        assert len(stats) == 1
        assert stats[0].call_count == 3
        assert stats[0].error_count == 1

    def test_error_count_in_session_stats(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, is_error=False)
        _make_call(tmp_db, is_error=True)
        _make_call(tmp_db, is_error=True)

        sessions = tmp_db.get_session_stats()
        assert sessions[0].total_errors == 2


class TestToolStats:
    def test_aggregation(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="add", elapsed_ms=100, result_size=20)
        _make_call(tmp_db, tool_name="add", elapsed_ms=200, result_size=30)

        stats = tmp_db.get_tool_stats()
        assert len(stats) == 1
        assert stats[0].call_count == 2
        assert stats[0].total_elapsed_ms == 300
        assert stats[0].avg_elapsed_ms == 150.0
        assert stats[0].total_result_size == 50

    def test_filter_by_server(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db, session_id="s1", server_name="alpha")
        call = ToolCall(
            session_id="s1",
            server_name="alpha",
            tool_name="foo",
            started_at=datetime.now().isoformat(),
            elapsed_ms=10,
        )
        tmp_db.record_call(call)

        stats = tmp_db.get_tool_stats(server_name="alpha")
        assert len(stats) == 1
        stats_empty = tmp_db.get_tool_stats(server_name="nonexistent")
        assert len(stats_empty) == 0


class TestSessionNaming:
    def test_auto_name_on_end(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="search")
        _make_call(tmp_db, tool_name="search")
        _make_call(tmp_db, tool_name="fetch")
        tmp_db.end_session("sess-001", total_calls=3)

        stats = tmp_db.get_session_stats()
        name = stats[0].session_name
        assert "test" in name
        assert "search" in name
        assert "3calls" in name

    def test_auto_name_includes_time_of_day(self, tmp_db: MeterDB) -> None:
        session = Session(
            id="sess-morning",
            server_name="myserver",
            server_command="python -m test",
            started_at="2026-03-11T09:30:00",
        )
        tmp_db.create_session(session)
        _make_call(tmp_db, session_id="sess-morning", tool_name="add")
        tmp_db.end_session("sess-morning", total_calls=1)

        stats = tmp_db.get_session_stats()
        assert "morning" in stats[0].session_name

    def test_rename_session(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db, tool_name="add")
        tmp_db.end_session("sess-001", total_calls=1)

        assert tmp_db.rename_session("sess-001", "debugging email")
        stats = tmp_db.get_session_stats()
        assert stats[0].session_name == "debugging email"

    def test_rename_by_name(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        tmp_db.end_session("sess-001", total_calls=0)

        stats = tmp_db.get_session_stats()
        auto_name = stats[0].session_name
        assert tmp_db.rename_session(auto_name, "better name")

        stats = tmp_db.get_session_stats()
        assert stats[0].session_name == "better name"

    def test_rename_nonexistent(self, tmp_db: MeterDB) -> None:
        assert tmp_db.rename_session("nope", "name") is False

    def test_session_without_calls(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        tmp_db.end_session("sess-001", total_calls=0)

        stats = tmp_db.get_session_stats()
        name = stats[0].session_name
        assert "test" in name
        assert "0calls" in name


class TestDailyTotals:
    def test_daily_totals(self, tmp_db: MeterDB) -> None:
        _make_session(tmp_db)
        _make_call(tmp_db)
        _make_call(tmp_db, is_error=True)

        totals = tmp_db.get_daily_totals(days=1)
        assert len(totals) == 1
        assert totals[0]["call_count"] == 2
        assert totals[0]["error_count"] == 1
