"""Circuit breaker tests for AgentMeter."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from time import time

import pytest

from agentmeter.db import MeterDB
from agentmeter.models import BreakerConfig, Session, ToolCall


@pytest.fixture()
def db(tmp_path: Path) -> MeterDB:
    return MeterDB(tmp_path / "test.db")


@pytest.fixture()
def db_with_session(db: MeterDB) -> tuple[MeterDB, str]:
    session_id = "breaker-test-001"
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
) -> ToolCall:
    return ToolCall(
        session_id=session_id,
        server_name=server_name,
        tool_name="search",
        started_at=datetime.now().isoformat(),
    )


class TestBreakerCRUD:
    """Test breaker config create/read/delete."""

    def test_set_breaker(self, db: MeterDB) -> None:
        config = BreakerConfig(max_calls=20, window_seconds=60)
        row_id = db.set_breaker(config)
        assert row_id > 0

        breakers = db.get_breakers()
        assert len(breakers) == 1
        assert breakers[0].max_calls == 20
        assert breakers[0].window_seconds == 60
        assert breakers[0].cooldown_seconds == 300

    def test_set_breaker_replaces_same_server(
        self, db: MeterDB,
    ) -> None:
        db.set_breaker(BreakerConfig(max_calls=20))
        db.set_breaker(BreakerConfig(max_calls=50))

        breakers = db.get_breakers()
        assert len(breakers) == 1
        assert breakers[0].max_calls == 50

    def test_set_breaker_different_servers(
        self, db: MeterDB,
    ) -> None:
        db.set_breaker(BreakerConfig(server_name=""))
        db.set_breaker(BreakerConfig(server_name="mailsift"))

        assert len(db.get_breakers()) == 2

    def test_get_breaker_for_server_specific(
        self, db: MeterDB,
    ) -> None:
        db.set_breaker(BreakerConfig(
            server_name="", max_calls=100,
        ))
        db.set_breaker(BreakerConfig(
            server_name="mailsift", max_calls=20,
        ))

        # Specific rule takes precedence
        config = db.get_breaker_for_server("mailsift")
        assert config is not None
        assert config.max_calls == 20

    def test_get_breaker_for_server_falls_back(
        self, db: MeterDB,
    ) -> None:
        db.set_breaker(BreakerConfig(
            server_name="", max_calls=100,
        ))

        # No specific rule, falls back to global
        config = db.get_breaker_for_server("anyserver")
        assert config is not None
        assert config.max_calls == 100

    def test_get_breaker_for_server_none(
        self, db: MeterDB,
    ) -> None:
        assert db.get_breaker_for_server("test") is None

    def test_clear_all(self, db: MeterDB) -> None:
        db.set_breaker(BreakerConfig(server_name=""))
        db.set_breaker(BreakerConfig(server_name="mail"))

        removed = db.clear_breakers()
        assert removed == 2
        assert db.get_breakers() == []

    def test_clear_by_server(self, db: MeterDB) -> None:
        db.set_breaker(BreakerConfig(server_name=""))
        db.set_breaker(BreakerConfig(server_name="mail"))

        removed = db.clear_breakers(server_name="mail")
        assert removed == 1
        assert len(db.get_breakers()) == 1


class TestBreakerTrips:
    """Test trip logging."""

    def test_record_trip(self, db: MeterDB) -> None:
        db.record_breaker_trip("testserver", 25, 60)

        trips = db.get_breaker_trips()
        assert len(trips) == 1
        assert trips[0]["server_name"] == "testserver"
        assert trips[0]["call_count"] == 25

    def test_trips_ordered_newest_first(self, db: MeterDB) -> None:
        db.record_breaker_trip("s1", 10, 30)
        db.record_breaker_trip("s2", 20, 60)

        trips = db.get_breaker_trips()
        assert trips[0]["server_name"] == "s2"

    def test_trips_limited(self, db: MeterDB) -> None:
        for i in range(20):
            db.record_breaker_trip(f"s{i}", i, 60)

        trips = db.get_breaker_trips(limit=5)
        assert len(trips) == 5


class TestBreakerProxy:
    """Test in-memory velocity tracking in the proxy."""

    def test_breaker_not_configured_allows_calls(self) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(Path("/tmp/test_breaker_proxy.db"))
        try:
            proxy = AgentMeterProxy(
                command="echo", args=[], db=db,
            )
            result = proxy._check_breaker("search")
            assert result is None
        finally:
            db.close()
            Path("/tmp/test_breaker_proxy.db").unlink(
                missing_ok=True,
            )

    def test_breaker_allows_under_threshold(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            max_calls=5, window_seconds=60,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[], db=db,
        )

        # 5 calls should be allowed (trips on >5)
        for _ in range(5):
            result = proxy._check_breaker("search")
            assert result is None

        db.close()

    def test_breaker_trips_over_threshold(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            max_calls=5,
            window_seconds=60,
            cooldown_seconds=10,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[], db=db,
        )

        # 5 calls OK, 6th trips
        for _ in range(5):
            proxy._check_breaker("search")
        result = proxy._check_breaker("search")

        assert result is not None
        assert result.isError is True
        assert "circuit breaker tripped" in result.content[0].text

        db.close()

    def test_breaker_blocks_during_cooldown(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            max_calls=3,
            window_seconds=60,
            cooldown_seconds=300,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[], db=db,
        )

        # Trip the breaker
        for _ in range(4):
            proxy._check_breaker("search")

        # Next call should be blocked (cooldown)
        result = proxy._check_breaker("search")
        assert result is not None
        assert "circuit breaker open" in result.content[0].text

        db.close()

    def test_breaker_resets_after_cooldown(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            max_calls=3,
            window_seconds=60,
            cooldown_seconds=1,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[], db=db,
        )

        # Trip the breaker
        for _ in range(4):
            proxy._check_breaker("search")

        # Simulate cooldown elapsed
        proxy._breaker_tripped_at = time() - 2

        # Should be allowed now
        result = proxy._check_breaker("search")
        assert result is None

        db.close()

    def test_breaker_window_expires_old_calls(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            max_calls=5,
            window_seconds=10,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[], db=db,
        )

        # Add old timestamps that should be pruned
        old_time = time() - 20
        for _ in range(5):
            proxy._call_timestamps.append(old_time)

        # New call should be fine (old ones expired)
        result = proxy._check_breaker("search")
        assert result is None

        db.close()

    def test_breaker_logs_trip_to_db(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            max_calls=3,
            window_seconds=60,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[], db=db,
        )

        # Trip the breaker
        for _ in range(4):
            proxy._check_breaker("search")

        trips = db.get_breaker_trips()
        assert len(trips) == 1
        assert trips[0]["call_count"] == 4

        db.close()

    def test_breaker_server_specific_ignores_other(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.proxy import AgentMeterProxy

        db = MeterDB(tmp_path / "test.db")
        db.set_breaker(BreakerConfig(
            server_name="mailsift",
            max_calls=3,
            window_seconds=60,
        ))

        proxy = AgentMeterProxy(
            command="echo", args=[],
            server_name="other", db=db,
        )

        # No breaker for "other" server
        for _ in range(10):
            result = proxy._check_breaker("search")
            assert result is None

        db.close()


class TestBreakerCLI:
    """Test breaker CLI commands."""

    def test_breaker_set_and_show(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        os.environ["AGENTMETER_DB"] = str(tmp_path / "test.db")

        runner = CliRunner()
        result = runner.invoke(main, [
            "breaker", "set", "20", "60",
        ])
        assert result.exit_code == 0
        assert "20 calls/60s" in result.output

        result = runner.invoke(main, ["breaker", "show"])
        assert result.exit_code == 0
        assert "20 calls/60s" in result.output

        del os.environ["AGENTMETER_DB"]

    def test_breaker_clear(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        os.environ["AGENTMETER_DB"] = str(tmp_path / "test.db")

        runner = CliRunner()
        runner.invoke(main, ["breaker", "set", "10", "30"])
        result = runner.invoke(main, [
            "breaker", "clear", "--yes",
        ])
        assert result.exit_code == 0
        assert "Removed 1" in result.output

        del os.environ["AGENTMETER_DB"]

    def test_breaker_show_empty(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        os.environ["AGENTMETER_DB"] = str(tmp_path / "test.db")

        runner = CliRunner()
        result = runner.invoke(main, ["breaker", "show"])
        assert result.exit_code == 0
        assert "No circuit breakers" in result.output

        del os.environ["AGENTMETER_DB"]
