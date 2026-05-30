# Spec: Cache Intelligence

## Problem

Prompt caching splits API costs into three token types at three different
rates (input 1x, cache write 1.25x, cache read 0.1x). AgentMeter already
tracks all three and shows the raw breakdown in `cost` and `strategy`. But
it doesn't answer the questions users actually care about:

1. **Is caching saving me money?** — No "savings" figure shown anywhere.
2. **How efficient is my caching?** — No hit-rate metric to track over time.
3. **Am I wasting money on cache writes?** — Short sessions pay the 1.25x
   write premium without enough reads to recoup it. No detection of this.

These are the highest-value insights AgentMeter can offer about prompt
caching, and nobody else provides them — not the API dashboard, not
LangSmith, not Helicone.

## Scope

Three features, each independent, all building on existing data:

1. **Cache efficiency metric** — computed and surfaced in CLI + dashboard
2. **Cache waste heuristics** — two new heuristics in `heuristics.py`
3. **Cache savings line** — added to `cost` and `strategy` output

No new data collection. No new DB tables. Everything derives from the
`SessionTokens` data already read from Claude Code JSONL transcripts.

---

## Feature 1: Cache Efficiency Metric

### What

A single percentage: how much of your input token volume was served from
cache rather than processed fresh.

```
cache_efficiency = cache_read_tokens / (cache_read_tokens + cache_creation_tokens + input_tokens) * 100
```

This measures the **token hit rate** — what fraction of all input-side tokens
were cache reads. Higher is better. Typical range: 60-90% for normal
sessions, <50% for short sessions, >90% for long sessions.

Note: output tokens are excluded from the denominator. They're a different
cost category and including them would dilute the signal.

### Where to surface

**`cost` (single session view):** Add a line after the token breakdown table:

```
  Cache efficiency:  78%
```

Only show when cache_read_tokens > 0 (skip for sessions with no caching).

**`cost` (recent sessions list):** Add to each session's detail block:

```
    Cache efficiency: 78%
```

**`strategy` (per-project):** Already shows `cache_read_pct` as a cost
percentage. Add the token-based efficiency metric alongside:

```
    Cache efficiency: 82% (token hit rate)
    Cost split: cache reads 74% | output 8% | input 18%
```

These are different numbers and both useful:
- Token hit rate = volume efficiency (how well caching works)
- Cost split = where money goes (cache reads are cheap per-token but high-volume)

**Dashboard API (`/api/strategy`):** Add `cacheEfficiency` field to each
project object. The dashboard already has `cacheHitPct` — rename this to
`cacheEfficiency` for consistency (it's already token-based).

### Implementation

Add a helper to `session_reader.py`:

```python
def cache_efficiency(tokens: SessionTokens) -> float | None:
    """Cache hit rate as percentage, or None if no input tokens."""
    input_total = (
        tokens.cache_read_tokens
        + tokens.cache_creation_tokens
        + tokens.input_tokens
    )
    if input_total == 0:
        return None
    return tokens.cache_read_tokens / input_total * 100
```

This is a pure function on existing data — no DB queries, no new fields.

Consumers call it where needed. No changes to models.py.

### Not doing

- Per-turn efficiency tracking (would need per-message data, not aggregated)
- Efficiency targets or colour coding (too early, need real-world baselines)
- Historical efficiency trends (wait for dashboard charting)

---

## Feature 2: Cache Waste Heuristics

Two new heuristics in `heuristics.py`, following the existing pattern of
plain functions returning `Finding` objects.

### Heuristic: `cache_write_waste`

**Detects:** Sessions where cache write cost exceeds cache read savings.
This happens in short sessions — you pay 1.25x to write the cache but
end before enough reads amortise the premium.

**Data source:** `SessionTokens` from JSONL (not tool_call DB).

**Trigger condition:**
```
cache_creation_tokens > 0
AND cache_read_tokens < cache_creation_tokens * 2
AND session has < 10 LLM calls
```

The threshold logic: each cache write costs 1.25x input. Each cache read
costs 0.1x input. To break even on a write, you need roughly
`1.25 / 0.1 = 12.5` reads of the same tokens. With < 10 LLM calls and
read tokens < 2x write tokens, the write premium wasn't recouped.

**Scope:** session

**Severity:** info (not the user's fault — it's just how short sessions work)

**Finding output:**
```python
Finding(
    pattern="cache_write_waste",
    severity="info",
    scope="session",
    summary="9 LLM calls — cache write premium ($0.42) exceeded read savings ($0.18)",
    advice=(
        "Short sessions don't benefit from caching. "
        "This isn't actionable — just explains why cost/call is higher."
    ),
    data={
        "llm_calls": 9,
        "cache_write_cost": 0.42,
        "cache_read_cost": 0.18,
        "cache_creation_tokens": 34000,
        "cache_read_tokens": 52000,
    },
)
```

### Heuristic: `low_cache_efficiency`

**Detects:** Sessions with significant token volume but poor cache hit rate.
Indicates the agent is restructuring prompts between turns (changing tool
definitions, reordering system blocks) which invalidates the cache prefix.

**Data source:** `SessionTokens` from JSONL.

**Trigger condition:**
```
total input-side tokens > 100,000
AND cache_efficiency < 40%
AND llm_call_count >= 15
```

The minimum thresholds filter out trivially small sessions where low
efficiency doesn't matter financially.

**Scope:** session

**Severity:** warning (this is unusual and worth investigating)

**Finding output:**
```python
Finding(
    pattern="low_cache_efficiency",
    severity="warning",
    scope="session",
    summary="Cache efficiency 23% over 42 LLM calls (680K input tokens mostly uncached)",
    advice=(
        "Most input tokens aren't hitting cache. Possible causes: "
        "long gaps between turns (>5min cache TTL), "
        "or agent restructuring prompts between calls."
    ),
    data={
        "cache_efficiency": 23.4,
        "llm_calls": 42,
        "input_tokens": 680000,
        "cache_read_tokens": 158000,
        "cache_creation_tokens": 320000,
    },
)
```

### Integration with AnalysisContext

These heuristics need `SessionTokens`, which currently isn't in
`AnalysisContext`. Two options:

**Option A (preferred):** Add an optional `tokens` field to `AnalysisContext`:

```python
@dataclass
class AnalysisContext:
    conn: sqlite3.Connection
    since: str | None = None
    project: str | None = None
    session_id: str | None = None
    tokens: SessionTokens | None = None   # NEW — from JSONL reader
    rate: RateCard | None = None           # NEW — for cost calculation
```

The spec for `AnalysisContext` already anticipated this:
> `# Future extensions: tokens, outcomes, model_costs`

Callers that have token data (coach review, advise with session filter)
populate it. Callers that don't (cross-session advise) leave it None.
The cache heuristics check `if ctx.tokens is None: return None`.

**Option B:** Have the heuristics call `session_reader` directly. Rejected
because heuristics should be pure analysis functions over provided data,
not I/O functions that read files.

### Heuristic list update

Add both to `analyse_session()` runners list. They're session-scope
because cache efficiency is meaningless when aggregated across sessions
(each session has its own cache lifecycle).

### Not doing

- Cross-session cache efficiency comparison (different sessions have
  different lengths, not comparable)
- Cache TTL detection (would need per-turn timestamps from JSONL, which
  we have but haven't parsed per-turn yet)
- Advice about 1-hour TTL (no agent uses it yet)

---

## Feature 3: Cache Savings Line

### What

Show the dollar amount saved by prompt caching: the difference between
what the session would have cost if all cache_read tokens were charged at
the full input rate, versus what they actually cost at the cache read rate.

```
savings = cache_read_tokens * (input_per_mtok - cached_per_mtok) / 1_000_000
```

This is concrete, correct, and compelling. Users see a real dollar figure
for what caching saved them.

### Where to surface

**`cost` (single session view):** Add after the total line:

```
  Token Breakdown                        Tokens        Cost
  ──────────────────────────────────────────────────────────
  Input (uncached)                       12,340    $  0.0617
  Cache creation                         45,678    $  0.2853
  Cache reads                           890,123    $  0.4451
  Output                                 34,567    $  0.5185
  ──────────────────────────────────────────────────────────
  Total                                 982,708    $  1.3106

  Cache saved:  $4.01 (75% less than without caching)
```

The percentage is `savings / (total_cost + savings) * 100` — i.e., what
fraction of the hypothetical uncached cost was avoided.

Only show when savings > $0.01 (skip trivial amounts).

**`cost` (recent sessions list):** Add a compact line:

```
    Cache saved:     $4.01 (75%)
```

**`strategy` (per-project):** Add to the project detail block, after
the cost split line:

```
    Cache saved: $18.42 (72% less than without caching)
```

Also add to the strategy recommendations: if a project has >$10 in
cache savings, note it as a positive — "Caching is working well for
{project}, saving ${X} over {days} days."

**Dashboard API:** Add `cacheSaved` to project objects. The dashboard
already has `savedVsUncached` but its calculation is rough
(`proj_cost / 0.1 if cache_pct > 50`). Replace with the exact formula.

### Implementation

Add to `session_reader.py`:

```python
def cache_savings(tokens: SessionTokens, rate: RateCard) -> float:
    """Dollar amount saved by cache reads vs full input rate."""
    if rate.cached_per_mtok >= rate.input_per_mtok:
        return 0.0  # no savings if cache isn't cheaper
    return (
        tokens.cache_read_tokens
        * (rate.input_per_mtok - rate.cached_per_mtok)
        / 1_000_000
    )
```

Pure function, no side effects. Callers format and display.

### Not doing

- Net savings (accounting for cache write premium) — this is technically
  more correct but harder to explain. The write premium is small (0.25x)
  and users already see cache_create_cost in the breakdown. Showing gross
  savings from reads is clearer.
- Savings over time chart (wait for dashboard charting)
- Savings alerts ("you saved $X today!") — too noisy

---

## Implementation Order

1. **Cache efficiency metric** — smallest change, immediate value. Add
   the helper function + display in `cost` and `strategy`. ~30 lines.

2. **Cache savings line** — similar scope. Add helper + display. Fix the
   dashboard's rough `savedVsUncached` calculation. ~40 lines.

3. **Cache waste heuristics** — requires extending `AnalysisContext` with
   `tokens` field and wiring it through callers. More plumbing but the
   heuristic functions themselves are small. ~80 lines.

## Files Changed

| File | Change |
|---|---|
| `session_reader.py` | Add `cache_efficiency()` and `cache_savings()` helpers |
| `cli_cost.py` | Display efficiency + savings in both views |
| `cli_strategy.py` | Display efficiency + savings per project |
| `cli_dashboard.py` | Fix `savedVsUncached`, add `cacheEfficiency` |
| `heuristics.py` | Add `AnalysisContext.tokens/rate`, add 2 heuristics |
| `cli_advise.py` | Pass tokens to AnalysisContext when available |
| `cli_coach.py` | Pass tokens to AnalysisContext (already has them) |

No changes to: `models.py`, `db/`, `hooks/`, `proxy.py`.

## Tests

All tests go in `tests/test_session_reader.py` (helper functions) and a
new `tests/test_heuristics.py` (cache heuristics). Follows existing
patterns: pure function tests for helpers, trigger/no-trigger tests for
heuristics, CLI output tests for display.

### Feature 1: `cache_efficiency()` — in `test_session_reader.py`

```python
class TestCacheEfficiency:
    """cache_efficiency() returns token hit rate as percentage."""

    def test_typical_session(self) -> None:
        """78% efficiency: most input tokens come from cache reads."""
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=2_000,
            cache_creation_tokens=20_000,
            cache_read_tokens=78_000,
        )
        result = cache_efficiency(tokens)
        assert result == pytest.approx(78.0)

    def test_no_caching(self) -> None:
        """0% when no cache reads at all."""
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=50_000,
            cache_creation_tokens=10_000,
            cache_read_tokens=0,
        )
        result = cache_efficiency(tokens)
        assert result == pytest.approx(0.0)

    def test_all_cached(self) -> None:
        """100% when everything is cache reads."""
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=100_000,
        )
        result = cache_efficiency(tokens)
        assert result == pytest.approx(100.0)

    def test_zero_tokens_returns_none(self) -> None:
        """None when no input tokens at all (empty session)."""
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens()
        result = cache_efficiency(tokens)
        assert result is None

    def test_excludes_output_tokens(self) -> None:
        """Output tokens don't affect efficiency (different cost category)."""
        from agentmeter.session_reader import cache_efficiency

        tokens = SessionTokens(
            input_tokens=10_000,
            cache_creation_tokens=10_000,
            cache_read_tokens=80_000,
            output_tokens=500_000,  # large output shouldn't matter
        )
        result = cache_efficiency(tokens)
        assert result == pytest.approx(80.0)
```

### Feature 2: `cache_savings()` — in `test_session_reader.py`

```python
class TestCacheSavings:
    """cache_savings() returns dollars saved by cache reads."""

    def test_savings_with_opus_rates(self) -> None:
        """10M cache reads at Opus rates: saved $135 vs full input."""
        from agentmeter.session_reader import cache_savings

        tokens = SessionTokens(cache_read_tokens=10_000_000)
        # Opus: input $15/Mtok, cached $1.50/Mtok
        saved = cache_savings(tokens, OPUS_RATE)
        # 10M * ($15 - $1.50) / 1M = $135
        assert saved == pytest.approx(135.0)

    def test_zero_cache_reads(self) -> None:
        """No savings when nothing was cached."""
        from agentmeter.session_reader import cache_savings

        tokens = SessionTokens(
            input_tokens=100_000,
            cache_read_tokens=0,
        )
        saved = cache_savings(tokens, OPUS_RATE)
        assert saved == 0.0

    def test_no_savings_when_cache_not_cheaper(self) -> None:
        """Edge case: if cached rate >= input rate, no savings."""
        from agentmeter.session_reader import cache_savings

        weird_rate = RateCard(
            model_id="weird",
            input_per_mtok=5.0,
            cached_per_mtok=5.0,  # same as input
        )
        tokens = SessionTokens(cache_read_tokens=1_000_000)
        saved = cache_savings(tokens, weird_rate)
        assert saved == 0.0

    def test_haiku_rates(self) -> None:
        """Different model, different savings rate."""
        from agentmeter.session_reader import cache_savings

        haiku_rate = RateCard(
            model_id="claude-haiku-4-5",
            input_per_mtok=0.8,
            cached_per_mtok=0.08,
        )
        tokens = SessionTokens(cache_read_tokens=5_000_000)
        # 5M * ($0.80 - $0.08) / 1M = $3.60
        saved = cache_savings(tokens, haiku_rate)
        assert saved == pytest.approx(3.6)

    def test_small_session_small_savings(self) -> None:
        """Short session: savings exist but are small."""
        from agentmeter.session_reader import cache_savings

        tokens = SessionTokens(cache_read_tokens=50_000)
        # 50K * ($15 - $1.50) / 1M = $0.675
        saved = cache_savings(tokens, OPUS_RATE)
        assert saved == pytest.approx(0.675)
```

### Feature 3: Cache waste heuristics — new `tests/test_heuristics.py`

These follow the existing heuristic test pattern: set up an
`AnalysisContext` with the right data, assert the heuristic fires or
doesn't fire.

Because the cache heuristics use `SessionTokens` (not the DB), they
don't need the `tmp_db` fixture — just a context with `tokens` and
`rate` populated.

```python
class TestCacheWriteWaste:
    """Heuristic: cache_write_waste — short sessions with unrecouped writes."""

    def test_fires_for_short_session_with_high_writes(self) -> None:
        """9 LLM calls, more writes than reads = waste detected."""
        from agentmeter.heuristics import _cache_write_waste, AnalysisContext

        ctx = AnalysisContext(
            conn=None,  # not used by this heuristic
            tokens=SessionTokens(
                llm_call_count=9,
                cache_creation_tokens=50_000,
                cache_read_tokens=40_000,  # < 2x writes
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
        """30 LLM calls with lots of cache reads = caching is working."""
        from agentmeter.heuristics import _cache_write_waste, AnalysisContext

        ctx = AnalysisContext(
            conn=None,
            tokens=SessionTokens(
                llm_call_count=30,
                cache_creation_tokens=50_000,
                cache_read_tokens=500_000,  # 10x writes
                input_tokens=5_000,
            ),
            rate=OPUS_RATE,
        )
        result = _cache_write_waste(ctx)
        assert result is None

    def test_does_not_fire_without_cache_writes(self) -> None:
        """No cache creation = nothing to waste."""
        from agentmeter.heuristics import _cache_write_waste, AnalysisContext

        ctx = AnalysisContext(
            conn=None,
            tokens=SessionTokens(
                llm_call_count=5,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                input_tokens=10_000,
            ),
            rate=OPUS_RATE,
        )
        result = _cache_write_waste(ctx)
        assert result is None

    def test_does_not_fire_without_tokens(self) -> None:
        """No token data = skip gracefully."""
        from agentmeter.heuristics import _cache_write_waste, AnalysisContext

        ctx = AnalysisContext(conn=None, tokens=None)
        result = _cache_write_waste(ctx)
        assert result is None


class TestLowCacheEfficiency:
    """Heuristic: low_cache_efficiency — high volume, poor hit rate."""

    def test_fires_for_low_efficiency_high_volume(self) -> None:
        """42 LLM calls, 680K input tokens, only 23% cached."""
        from agentmeter.heuristics import _low_cache_efficiency, AnalysisContext

        ctx = AnalysisContext(
            conn=None,
            tokens=SessionTokens(
                llm_call_count=42,
                input_tokens=400_000,
                cache_creation_tokens=122_000,
                cache_read_tokens=158_000,  # 23% of 680K
            ),
            rate=OPUS_RATE,
        )
        result = _low_cache_efficiency(ctx)
        assert result is not None
        assert result.pattern == "low_cache_efficiency"
        assert result.severity == "warning"
        assert result.data["cache_efficiency"] < 40

    def test_does_not_fire_for_good_efficiency(self) -> None:
        """Same volume but 85% cached = fine."""
        from agentmeter.heuristics import _low_cache_efficiency, AnalysisContext

        ctx = AnalysisContext(
            conn=None,
            tokens=SessionTokens(
                llm_call_count=42,
                input_tokens=50_000,
                cache_creation_tokens=50_000,
                cache_read_tokens=580_000,  # 85% of 680K
            ),
            rate=OPUS_RATE,
        )
        result = _low_cache_efficiency(ctx)
        assert result is None

    def test_does_not_fire_for_small_session(self) -> None:
        """Low efficiency but <100K tokens = not worth flagging."""
        from agentmeter.heuristics import _low_cache_efficiency, AnalysisContext

        ctx = AnalysisContext(
            conn=None,
            tokens=SessionTokens(
                llm_call_count=20,
                input_tokens=30_000,
                cache_creation_tokens=20_000,
                cache_read_tokens=10_000,  # 17% but only 60K total
            ),
            rate=OPUS_RATE,
        )
        result = _low_cache_efficiency(ctx)
        assert result is None

    def test_does_not_fire_for_few_llm_calls(self) -> None:
        """Low efficiency but <15 LLM calls = too short to judge."""
        from agentmeter.heuristics import _low_cache_efficiency, AnalysisContext

        ctx = AnalysisContext(
            conn=None,
            tokens=SessionTokens(
                llm_call_count=10,
                input_tokens=80_000,
                cache_creation_tokens=30_000,
                cache_read_tokens=20_000,  # 15% but only 10 calls
            ),
            rate=OPUS_RATE,
        )
        result = _low_cache_efficiency(ctx)
        assert result is None

    def test_does_not_fire_without_tokens(self) -> None:
        """No token data = skip gracefully."""
        from agentmeter.heuristics import _low_cache_efficiency, AnalysisContext

        ctx = AnalysisContext(conn=None, tokens=None)
        result = _low_cache_efficiency(ctx)
        assert result is None
```

### CLI output tests — in `test_session_reader.py`

```python
class TestCostCLICacheDisplay:
    """Verify cache efficiency and savings appear in cost output."""

    def test_cost_shows_cache_efficiency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Single session cost view includes 'Cache efficiency: XX%'."""
        # Setup: create a session in DB, write a JSONL with cache tokens,
        # invoke `agentmeter cost <session-id>`, assert output contains
        # "Cache efficiency:" with a percentage.
        #
        # Full fixture setup follows test_cost_no_sessions pattern —
        # details deferred to implementation since they depend on the
        # exact display format chosen.
        pass  # placeholder — implement when building

    def test_cost_shows_cache_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Single session cost view includes 'Cache saved: $X.XX'."""
        pass  # placeholder — implement when building

    def test_cost_hides_cache_lines_when_no_caching(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Don't show efficiency or savings when cache_read_tokens == 0."""
        pass  # placeholder — implement when building
```

### Test count

| Area | Tests | Notes |
|---|---|---|
| `cache_efficiency()` | 5 | Pure function, all edge cases |
| `cache_savings()` | 5 | Pure function, different rates |
| `_cache_write_waste` heuristic | 4 | Fire, no-fire, no-data, no-writes |
| `_low_cache_efficiency` heuristic | 5 | Fire, no-fire, small, few calls, no-data |
| CLI display | 3 | Efficiency shown, savings shown, hidden when N/A |
| **Total** | **22** | |

The pure function tests (10) are concrete and ready to write as-is. The
heuristic tests (9) are concrete but need the `AnalysisContext.tokens`
field to exist first. The CLI tests (3) are placeholders — they need
JSONL fixtures wired through the DB, which depends on exact display
formatting decisions.

## Messaging Value

These three features together give AgentMeter a unique angle on prompt
caching that no competitor offers:

- **Helicone/Portkey:** Show per-request token breakdown (same as API response)
- **LangSmith/Langfuse:** Don't distinguish cache token types at all
- **API dashboard:** Shows aggregate usage, no per-session or per-project view
- **AgentMeter:** Cache efficiency trends, waste detection, concrete savings
  figures, all local, no account required

The pitch: "Prompt caching makes your costs more complex, not simpler.
AgentMeter shows you whether caching is actually working — and how much
it's saving you."
