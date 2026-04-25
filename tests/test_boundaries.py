"""Boundary and edge case tests for AgentMeter."""

from __future__ import annotations

from datetime import datetime

from agentmeter.db import MeterDB
from agentmeter.models import Session, ToolCall


def _seed_session(
    db: MeterDB,
    session_id: str = "sess-001",
    server_name: str = "test",
    started_at: str | None = None,
) -> None:
    session = Session(
        id=session_id,
        server_name=server_name,
        server_command="python -m test",
        started_at=started_at or datetime.now().isoformat(),
    )
    db.create_session(session)


def _seed_call(
    db: MeterDB,
    session_id: str = "sess-001",
    tool_name: str = "add",
    elapsed_ms: int = 50,
    result_size: int = 10,
    is_error: bool = False,
) -> None:
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


# ── String boundary tests ──────────────────────────────────────────


class TestStringBoundaries:
    def test_empty_tool_name(self, tmp_db: MeterDB) -> None:
        """Empty tool name should be storable and retrievable."""
        _seed_session(tmp_db)
        _seed_call(tmp_db, tool_name="")
        calls = tmp_db.get_recent_calls()
        assert len(calls) == 1
        assert calls[0].tool_name == ""

    def test_single_char_tool_name(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        _seed_call(tmp_db, tool_name="x")
        calls = tmp_db.get_recent_calls(tool_name="x")
        assert len(calls) == 1

    def test_very_long_tool_name(self, tmp_db: MeterDB) -> None:
        """A 10K character tool name should be storable."""
        _seed_session(tmp_db)
        long_name = "t" * 10000
        _seed_call(tmp_db, tool_name=long_name)
        calls = tmp_db.get_recent_calls()
        assert calls[0].tool_name == long_name

    def test_unicode_tool_name(self, tmp_db: MeterDB) -> None:
        """Unicode chars in tool names — emoji, CJK, combining."""
        _seed_session(tmp_db)
        names = ["搜索", "🔍search", "café", "naïve", "a\u0301"]  # combining accent
        for name in names:
            _seed_call(tmp_db, tool_name=name)
        calls = tmp_db.get_recent_calls()
        stored_names = {c.tool_name for c in calls}
        for name in names:
            assert name in stored_names, f"Unicode name {name!r} not preserved"

    def test_null_bytes_in_tool_name(self, tmp_db: MeterDB) -> None:
        """Null bytes in tool names should be handled safely."""
        _seed_session(tmp_db)
        name_with_null = "tool\x00name"
        _seed_call(tmp_db, tool_name=name_with_null)
        calls = tmp_db.get_recent_calls()
        assert len(calls) == 1

    def test_newlines_in_tool_name(self, tmp_db: MeterDB) -> None:
        """Newlines in tool name should be stored literally."""
        _seed_session(tmp_db)
        _seed_call(tmp_db, tool_name="line1\nline2\ttab")
        calls = tmp_db.get_recent_calls()
        assert calls[0].tool_name == "line1\nline2\ttab"

    def test_empty_server_name(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db, server_name="")
        stats = tmp_db.get_session_stats()
        assert len(stats) == 1
        assert stats[0].server_name == ""


# ── Numeric boundary tests ─────────────────────────────────────────


class TestNumericBoundaries:
    def test_zero_elapsed_ms(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        _seed_call(tmp_db, elapsed_ms=0)
        calls = tmp_db.get_recent_calls()
        assert calls[0].elapsed_ms == 0

    def test_large_elapsed_ms(self, tmp_db: MeterDB) -> None:
        """Very large elapsed_ms (simulating a 24-hour tool call)."""
        _seed_session(tmp_db)
        _seed_call(tmp_db, elapsed_ms=86_400_000)
        calls = tmp_db.get_recent_calls()
        assert calls[0].elapsed_ms == 86_400_000

    def test_zero_result_size(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        _seed_call(tmp_db, result_size=0)
        calls = tmp_db.get_recent_calls()
        assert calls[0].result_size == 0

    def test_large_result_size(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        _seed_call(tmp_db, result_size=100_000_000)  # 100MB
        calls = tmp_db.get_recent_calls()
        assert calls[0].result_size == 100_000_000


# ── Limit parameter tests ──────────────────────────────────────────


class TestLimitBoundaries:
    def test_limit_zero_recent_calls(self, tmp_db: MeterDB) -> None:
        """limit=0 should return no results."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        calls = tmp_db.get_recent_calls(limit=0)
        assert len(calls) == 0

    def test_limit_one(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        for _ in range(5):
            _seed_call(tmp_db)
        calls = tmp_db.get_recent_calls(limit=1)
        assert len(calls) == 1

    def test_limit_exceeds_data(self, tmp_db: MeterDB) -> None:
        """limit=999999 with only 2 rows should return 2."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        _seed_call(tmp_db)
        calls = tmp_db.get_recent_calls(limit=999999)
        assert len(calls) == 2

    def test_limit_zero_session_stats(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        stats = tmp_db.get_session_stats(limit=0)
        assert len(stats) == 0

    def test_daily_totals_days_zero(self, tmp_db: MeterDB) -> None:
        """days=0 should return only today or empty."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        totals = tmp_db.get_daily_totals(days=0)
        # With days=0, the since date is today — should include today's data
        assert isinstance(totals, list)

    def test_daily_totals_days_one(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        totals = tmp_db.get_daily_totals(days=1)
        assert len(totals) >= 1

    def test_daily_totals_days_large(self, tmp_db: MeterDB) -> None:
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        totals = tmp_db.get_daily_totals(days=3650)
        assert isinstance(totals, list)


# ── Time boundary tests ────────────────────────────────────────────


class TestTimeBoundaries:
    def test_since_far_future(self, tmp_db: MeterDB) -> None:
        """Filtering with a far-future date should return nothing."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        assert tmp_db.get_total_calls(since="2099-01-01") == 0
        stats = tmp_db.get_tool_stats(since="2099-01-01")
        assert len(stats) == 0

    def test_since_epoch(self, tmp_db: MeterDB) -> None:
        """Filtering from epoch should return everything."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        assert tmp_db.get_total_calls(since="1970-01-01") == 1

    def test_malformed_since_date(self, tmp_db: MeterDB) -> None:
        """Malformed date in since should not crash or corrupt."""
        _seed_session(tmp_db)
        _seed_call(tmp_db)
        # SQLite string comparison means "not-a-date" just won't match —
        # it shouldn't crash
        try:
            result = tmp_db.get_total_calls(since="not-a-date")
            assert isinstance(result, int)
        except Exception:
            pass  # Exception is acceptable too
        # Data must be intact
        assert tmp_db.get_total_calls() == 1

    def test_session_name_with_midnight_hour(self, tmp_db: MeterDB) -> None:
        """Session at midnight should get 'night' label."""
        _seed_session(tmp_db, started_at="2026-03-20T00:30:00")
        _seed_call(tmp_db, tool_name="search")
        tmp_db.end_session("sess-001", total_calls=1)
        stats = tmp_db.get_session_stats()
        assert "night" in stats[0].session_name

    def test_session_name_with_malformed_time(self, tmp_db: MeterDB) -> None:
        """Malformed timestamp shouldn't crash session naming."""
        _seed_session(tmp_db, started_at="garbage")
        _seed_call(tmp_db, tool_name="search")
        # Should not crash
        tmp_db.end_session("sess-001", total_calls=1)
        stats = tmp_db.get_session_stats()
        assert len(stats) == 1


# ── Empty DB tests ──────────────────────────────────────────────────


class TestEmptyDB:
    def test_get_recent_calls_empty(self, tmp_db: MeterDB) -> None:
        assert tmp_db.get_recent_calls() == []

    def test_get_total_calls_empty(self, tmp_db: MeterDB) -> None:
        assert tmp_db.get_total_calls() == 0

    def test_get_tool_stats_empty(self, tmp_db: MeterDB) -> None:
        assert tmp_db.get_tool_stats() == []

    def test_get_session_stats_empty(self, tmp_db: MeterDB) -> None:
        assert tmp_db.get_session_stats() == []

    def test_get_daily_totals_empty(self, tmp_db: MeterDB) -> None:
        assert tmp_db.get_daily_totals() == []

    def test_rename_nonexistent_session(self, tmp_db: MeterDB) -> None:
        assert tmp_db.rename_session("nope", "name") is False


# ── High volume tests ───────────────────────────────────────────────


class TestHighVolume:
    def test_many_calls_in_session(self, tmp_db: MeterDB) -> None:
        """500 calls in one session should work correctly."""
        _seed_session(tmp_db)
        for i in range(500):
            _seed_call(tmp_db, tool_name=f"tool_{i % 10}")
        assert tmp_db.get_total_calls() == 500
        stats = tmp_db.get_tool_stats()
        assert sum(s.call_count for s in stats) == 500

    def test_many_sessions(self, tmp_db: MeterDB) -> None:
        """50 sessions should all be trackable."""
        for i in range(50):
            _seed_session(tmp_db, session_id=f"sess-{i:03d}")
            _seed_call(tmp_db, session_id=f"sess-{i:03d}")
            tmp_db.end_session(f"sess-{i:03d}", total_calls=1)
        stats = tmp_db.get_session_stats(limit=100)
        assert len(stats) == 50
