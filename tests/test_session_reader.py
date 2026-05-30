"""Tests for session token reader — written from spec before implementation.

Tests the contract defined in specs/session-token-reader.md:
- Read JSONL, extract tokens from assistant messages
- Skip non-assistant message types
- Handle missing files, partial files, malformed lines
- Cost calculation with rate card
- Project slug derivation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentmeter.models import RateCard, SessionTokens

# ── Sample JSONL fixtures ───────────────────────────────────────────

ASSISTANT_MSG = {
    "type": "assistant",
    "sessionId": "test-session-1",
    "timestamp": 1779071325836,
    "message": {
        "model": "claude-opus-4-6",
        "usage": {
            "input_tokens": 100,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 10000,
            "output_tokens": 200,
            "service_tier": "standard",
        },
    },
}

ASSISTANT_MSG_2 = {
    "type": "assistant",
    "sessionId": "test-session-1",
    "timestamp": 1779071326000,
    "message": {
        "model": "claude-opus-4-6",
        "usage": {
            "input_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8000,
            "output_tokens": 150,
        },
    },
}

USER_MSG = {
    "type": "user",
    "sessionId": "test-session-1",
    "message": "hello",
}

PROGRESS_MSG = {
    "type": "progress",
    "sessionId": "test-session-1",
}

SYSTEM_MSG = {
    "type": "system",
    "sessionId": "test-session-1",
    "message": "context loaded",
}

OPUS_RATE = RateCard(
    model_id="claude-opus-4-6",
    display_name="Claude Opus 4.6",
    input_per_mtok=15.0,
    output_per_mtok=75.0,
    cached_per_mtok=1.5,
)


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    """Write a list of dicts as JSONL."""
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


# ── Token extraction tests ──────────────────────────────────────────


class TestReadSessionTokens:
    """Spec: read JSONL, extract tokens from assistant messages."""

    def test_extracts_tokens_from_single_assistant_message(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [ASSISTANT_MSG])

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.input_tokens == 100
        assert result.cache_creation_tokens == 500
        assert result.cache_read_tokens == 10000
        assert result.output_tokens == 200
        assert result.llm_call_count == 1
        assert result.model_id == "claude-opus-4-6"

    def test_sums_tokens_across_multiple_assistant_messages(
        self, tmp_path: Path,
    ) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [ASSISTANT_MSG, ASSISTANT_MSG_2])

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.input_tokens == 150  # 100 + 50
        assert result.cache_creation_tokens == 500  # 500 + 0
        assert result.cache_read_tokens == 18000  # 10000 + 8000
        assert result.output_tokens == 350  # 200 + 150
        assert result.llm_call_count == 2

    def test_skips_non_assistant_messages(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            USER_MSG, PROGRESS_MSG, SYSTEM_MSG, ASSISTANT_MSG, PROGRESS_MSG,
        ])

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.llm_call_count == 1
        assert result.input_tokens == 100

    def test_returns_none_for_missing_file(self) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        result = read_session_tokens_from_file(Path("/nonexistent/file.jsonl"))
        assert result is None

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.llm_call_count == 0
        assert result.input_tokens == 0

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        jsonl = tmp_path / "session.jsonl"
        with open(jsonl, "w") as f:
            f.write("not json at all\n")
            f.write(json.dumps(ASSISTANT_MSG) + "\n")
            f.write("{broken json\n")
            f.write(json.dumps(ASSISTANT_MSG_2) + "\n")

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.llm_call_count == 2
        assert result.input_tokens == 150

    def test_handles_assistant_without_usage(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        no_usage = {
            "type": "assistant",
            "message": {"model": "claude-opus-4-6", "content": "hello"},
        }
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [no_usage, ASSISTANT_MSG])

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.llm_call_count == 1  # only the one with usage
        assert result.input_tokens == 100

    def test_handles_assistant_without_message(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import read_session_tokens_from_file

        no_message = {"type": "assistant", "sessionId": "x"}
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [no_message, ASSISTANT_MSG])

        result = read_session_tokens_from_file(jsonl)
        assert result is not None
        assert result.llm_call_count == 1


# ── Cache efficiency tests ─────────────────────────────────────────


class TestCacheEfficiency:
    """cache_efficiency() returns token hit rate as percentage."""

    def test_typical_session(self) -> None:
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=2_000,
            cache_creation_tokens=20_000,
            cache_read_tokens=78_000,
        )
        assert cache_efficiency(tokens) == pytest.approx(78.0)

    def test_no_caching(self) -> None:
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=50_000,
            cache_creation_tokens=10_000,
            cache_read_tokens=0,
        )
        assert cache_efficiency(tokens) == pytest.approx(0.0)

    def test_all_cached(self) -> None:
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=100_000,
        )
        assert cache_efficiency(tokens) == pytest.approx(100.0)

    def test_zero_tokens_returns_none(self) -> None:
        from agentmeter.session_reader import cache_efficiency

        assert cache_efficiency(SessionTokens()) is None

    def test_excludes_output_tokens(self) -> None:
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=10_000,
            cache_creation_tokens=10_000,
            cache_read_tokens=80_000,
            output_tokens=500_000,
        )
        assert cache_efficiency(tokens) == pytest.approx(80.0)


# ── Cache savings tests ───────────────────────────────────────────


class TestCacheSavings:
    """cache_savings() returns dollars saved by cache reads."""

    def test_savings_with_opus_rates(self) -> None:
        from agentmeter.session_reader import cache_savings

        tokens = SessionTokens(cache_read_tokens=10_000_000)
        # 10M * ($15 - $1.50) / 1M = $135
        assert cache_savings(tokens, OPUS_RATE) == pytest.approx(135.0)

    def test_zero_cache_reads(self) -> None:
        from agentmeter.session_reader import cache_savings

        tokens = SessionTokens(input_tokens=100_000, cache_read_tokens=0)
        assert cache_savings(tokens, OPUS_RATE) == 0.0

    def test_no_savings_when_cache_not_cheaper(self) -> None:
        from agentmeter.session_reader import cache_savings

        weird_rate = RateCard(
            model_id="weird",
            input_per_mtok=5.0,
            cached_per_mtok=5.0,
        )
        tokens = SessionTokens(cache_read_tokens=1_000_000)
        assert cache_savings(tokens, weird_rate) == 0.0

    def test_haiku_rates(self) -> None:
        from agentmeter.session_reader import cache_savings

        haiku_rate = RateCard(
            model_id="claude-haiku-4-5",
            input_per_mtok=0.8,
            cached_per_mtok=0.08,
        )
        tokens = SessionTokens(cache_read_tokens=5_000_000)
        # 5M * ($0.80 - $0.08) / 1M = $3.60
        assert cache_savings(tokens, haiku_rate) == pytest.approx(3.6)

    def test_small_session_small_savings(self) -> None:
        from agentmeter.session_reader import cache_savings

        tokens = SessionTokens(cache_read_tokens=50_000)
        # 50K * ($15 - $1.50) / 1M = $0.675
        assert cache_savings(tokens, OPUS_RATE) == pytest.approx(0.675)


# ── Cost calculation tests ──────────────────────────────────────────


class TestCalculateSessionCost:
    """Spec: cost = tokens * rate_card rates."""

    def test_calculates_cost_from_tokens(self) -> None:
        from agentmeter.session_reader import calculate_session_cost

        tokens = SessionTokens(
            input_tokens=1_000_000,       # 1M tokens
            cache_creation_tokens=500_000,
            cache_read_tokens=10_000_000,
            output_tokens=100_000,
        )

        cost = calculate_session_cost(tokens, OPUS_RATE)

        # 1M input * $15/Mtok = $15.00
        assert cost.input_cost == pytest.approx(15.0)
        # 500K cache create * $15/Mtok = $7.50
        assert cost.cache_create_cost == pytest.approx(7.5)
        # 10M cache read * $1.50/Mtok = $15.00
        assert cost.cache_read_cost == pytest.approx(15.0)
        # 100K output * $75/Mtok = $7.50
        assert cost.output_cost == pytest.approx(7.5)
        # Total
        assert cost.total_cost == pytest.approx(45.0)

    def test_zero_tokens_zero_cost(self) -> None:
        from agentmeter.session_reader import calculate_session_cost

        tokens = SessionTokens()
        cost = calculate_session_cost(tokens, OPUS_RATE)
        assert cost.total_cost == 0.0

    def test_different_model_rates(self) -> None:
        from agentmeter.session_reader import calculate_session_cost

        haiku_rate = RateCard(
            model_id="claude-haiku-4-5",
            input_per_mtok=0.8,
            output_per_mtok=4.0,
            cached_per_mtok=0.08,
        )
        tokens = SessionTokens(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        cost = calculate_session_cost(tokens, haiku_rate)
        assert cost.input_cost == pytest.approx(0.8)
        assert cost.output_cost == pytest.approx(4.0)
        assert cost.total_cost == pytest.approx(4.8)


# ── Project slug derivation tests ───────────────────────────────────


class TestProjectSlug:
    """Spec: derive Claude Code project slug from directory path."""

    def test_standard_path(self) -> None:
        from agentmeter.session_reader import derive_project_slug

        slug = derive_project_slug("/media/aa/LargeBackup/MainApps/AgentMeter")
        assert slug == "-media-aa-LargeBackup-MainApps-AgentMeter"

    def test_home_directory(self) -> None:
        from agentmeter.session_reader import derive_project_slug

        slug = derive_project_slug("/home/user/projects/myapp")
        assert slug == "-home-user-projects-myapp"

    def test_root_path(self) -> None:
        from agentmeter.session_reader import derive_project_slug

        slug = derive_project_slug("/")
        assert slug == ""

    def test_empty_path(self) -> None:
        from agentmeter.session_reader import derive_project_slug

        slug = derive_project_slug("")
        assert slug == ""


# ── JSONL file discovery tests ──────────────────────────────────────


class TestFindSessionJsonl:
    """Spec: find JSONL at ~/.claude/projects/<slug>/<session-id>.jsonl"""

    def test_finds_existing_file(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import find_session_jsonl

        # Simulate ~/.claude/projects/<slug>/
        slug = "-home-user-myproject"
        projects_dir = tmp_path / "projects" / slug
        projects_dir.mkdir(parents=True)
        jsonl = projects_dir / "abc123.jsonl"
        jsonl.write_text("{}\n")

        result = find_session_jsonl(
            "abc123",
            "/home/user/myproject",
            claude_dir=tmp_path,
        )
        assert result == jsonl

    def test_returns_none_for_missing_session(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import find_session_jsonl

        result = find_session_jsonl(
            "nonexistent",
            "/home/user/myproject",
            claude_dir=tmp_path,
        )
        assert result is None

    def test_returns_none_for_missing_project(self, tmp_path: Path) -> None:
        from agentmeter.session_reader import find_session_jsonl

        result = find_session_jsonl(
            "abc123",
            "/no/such/project",
            claude_dir=tmp_path,
        )
        assert result is None


# ── CLI cost command tests ──────────────────────────────────────────


class TestCostCLI:
    """Test the agentmeter cost CLI command."""

    def test_cost_no_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        monkeypatch.setenv("AGENTMETER_DB", str(tmp_path / "empty.db"))
        runner = CliRunner()
        result = runner.invoke(main, ["cost"])
        assert result.exit_code == 0
        assert (
            "No sessions" in result.output
            or "No session transcripts" in result.output
        )

    def test_cost_missing_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        monkeypatch.setenv("AGENTMETER_DB", str(tmp_path / "test.db"))
        runner = CliRunner()
        result = runner.invoke(main, ["cost", "nonexistent-session"])
        assert result.exit_code == 0
        assert "not found" in result.output
