"""Shared pytest fixtures for AgentMeter tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentmeter.db import MeterDB


@pytest.fixture
def tmp_db(tmp_path: Path) -> MeterDB:
    """Create a MeterDB backed by a temporary file."""
    db = MeterDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def test_server_path() -> str:
    """Path to the minimal test MCP server."""
    return str(Path(__file__).parent / "test_server.py")
