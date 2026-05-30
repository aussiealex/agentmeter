"""Tests for cache intelligence heuristics."""

from __future__ import annotations

from agentmeter.heuristics import (
    AnalysisContext,
    _cache_write_waste,
    _low_cache_efficiency,
)
from agentmeter.models import RateCard, SessionTokens

OPUS_RATE = RateCard(
    model_id="claude-opus-4-6",
    display_name="Claude Opus 4.6",
    input_per_mtok=15.0,
    output_per_mtok=75.0,
    cached_per_mtok=1.5,
    cache_write_per_mtok=18.75,
)


class TestCacheWriteWaste:
    """Heuristic: short sessions with unrecouped cache writes."""

    def test_fires_for_short_session_with_high_writes(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=9,
                cache_creation_tokens=50_000,
                cache_read_tokens=40_000,
                input_tokens=5_000,
            ),
            rate=OPUS_RATE,
        )
        result = _cache_write_waste(ctx)
        assert result is not None
        assert result.pattern == "cache_write_waste"
        assert result.severity == "info"
        assert result.data["llm_calls"] == 9

    def test_does_not_fire_for_long_session(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=30,
                cache_creation_tokens=50_000,
                cache_read_tokens=500_000,
                input_tokens=5_000,
            ),
            rate=OPUS_RATE,
        )
        assert _cache_write_waste(ctx) is None

    def test_does_not_fire_without_cache_writes(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=5,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                input_tokens=10_000,
            ),
            rate=OPUS_RATE,
        )
        assert _cache_write_waste(ctx) is None

    def test_does_not_fire_without_tokens(self) -> None:
        ctx = AnalysisContext(conn=None, tokens=None)  # type: ignore[arg-type]
        assert _cache_write_waste(ctx) is None


class TestLowCacheEfficiency:
    """Heuristic: high volume, poor cache hit rate."""

    def test_fires_for_low_efficiency_high_volume(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=42,
                input_tokens=400_000,
                cache_creation_tokens=122_000,
                cache_read_tokens=158_000,
            ),
        )
        result = _low_cache_efficiency(ctx)
        assert result is not None
        assert result.pattern == "low_cache_efficiency"
        assert result.severity == "warning"
        assert result.data["cache_efficiency"] < 40

    def test_does_not_fire_for_good_efficiency(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=42,
                input_tokens=50_000,
                cache_creation_tokens=50_000,
                cache_read_tokens=580_000,
            ),
        )
        assert _low_cache_efficiency(ctx) is None

    def test_does_not_fire_for_small_session(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=20,
                input_tokens=30_000,
                cache_creation_tokens=20_000,
                cache_read_tokens=10_000,
            ),
        )
        assert _low_cache_efficiency(ctx) is None

    def test_does_not_fire_for_few_llm_calls(self) -> None:
        ctx = AnalysisContext(
            conn=None,  # type: ignore[arg-type]
            tokens=SessionTokens(
                llm_call_count=10,
                input_tokens=80_000,
                cache_creation_tokens=30_000,
                cache_read_tokens=20_000,
            ),
        )
        assert _low_cache_efficiency(ctx) is None

    def test_does_not_fire_without_tokens(self) -> None:
        ctx = AnalysisContext(conn=None, tokens=None)  # type: ignore[arg-type]
        assert _low_cache_efficiency(ctx) is None
