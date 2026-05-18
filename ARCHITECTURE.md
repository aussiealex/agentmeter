# Architecture

> Technical reference for contributors and future sessions. For the project
> overview, read `PROJECT_BRIEF.md` first.

## Layers

AgentMeter has four layers. Each has one job. Don't cross boundaries.

```
┌─────────────────────────────────────────────────────────┐
│  CAPTURE LAYER — hooks/ and proxy.py                    │
│  Receives tool call data from agents, normalises it     │
└──────────────────────┬──────────────────────────────────┘
                       │ NormalisedToolEvent
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STORAGE LAYER — db/                                    │
│  SQLite writes and reads, schema, migrations            │
└──────────────────────┬──────────────────────────────────┘
                       │ Dataclasses (ToolCall, Session, etc.)
                       ▼
┌─────────────────────────────────────────────────────────┐
│  QUERY LAYER — db/analytics.py, db/calls.py, etc.      │
│  Aggregations, distributions, filtering                 │
└──────────────────────┬──────────────────────────────────┘
                       │ Dataclasses (ToolStats, DailyTotal, etc.)
                       ▼
┌─────────────────────────────────────────────────────────┐
│  DISPLAY LAYER — cli.py, cli_*.py                       │
│  Formatting, user interaction, output                   │
└─────────────────────────────────────────────────────────┘
```

**Rules:**
- Capture layer never formats output
- Query layer never writes data
- Display layer never touches SQLite directly
- New agents only touch the capture layer
- New analytics only touch the query layer
- New commands only touch the display layer
- The DB schema is the contract between all layers

## Capture Paths

### Hook Path (primary)

Every supported agent has a hook adapter in `hooks/`. The adapter:

1. Reads JSON from stdin (agent-specific format)
2. Skips MCP tools (`mcp__*` prefix) to avoid double-counting
3. Normalises the payload into a `NormalisedToolEvent`
4. Calls `hooks/base.py:record_event()` which writes to the DB

```
Agent stdin → hooks/<agent>.py → NormalisedToolEvent → base.record_event() → SQLite
```

Each adapter is ~80 lines. The shared logic in `base.py` handles:
- Session creation (idempotent via INSERT OR IGNORE)
- Tool call recording
- Project name extraction from working directory
- Timestamp generation

### MCP Proxy Path

`proxy.py` contains `AgentMeterProxy`, which:

1. Spawns a child MCP server as a subprocess
2. Re-exports the child's tools to the parent agent
3. Forwards all tool calls, recording metrics before/after
4. Checks budgets and circuit breakers before forwarding
5. Ends the session on shutdown (finally block)

```
Agent (stdio) → AgentMeterProxy → Child MCP Server (subprocess)
                     │
                     └→ SQLite (same DB as hooks)
```

The proxy is fully transparent — it never modifies tool call data.

## Hook Adapter Contract

To add a new agent, create `hooks/<agent>.py` with:

```python
"""<Agent Name> hook adapter."""
import json, sys
from agentmeter.hooks.base import extract_project, get_timestamp, record_event
from agentmeter.models import NormalisedToolEvent

AGENT = "<agent-name>"  # e.g. "cursor-ide"

def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    tool_name = data.get("tool_name", "")
    if tool_name.startswith("mcp__"):
        return

    # ... extract fields from agent-specific payload ...

    event = NormalisedToolEvent(
        session_id=..., agent=AGENT, tool_name=...,
        input_size=..., result_size=..., result_type=...,
        model_id=..., project=extract_project(cwd), cwd=...,
        timestamp=..., elapsed_ms=0,
        arguments_json=args_str[:1000], result_json=result_str[:2000],
    )
    record_event(event)

if __name__ == "__main__":
    main()
```

**Rules for adapters:**
- Never write to stdout (agents parse stdout)
- Never raise exceptions — catch everything, log to stderr, exit 0
- Must be fast (<5ms) — minimal imports, no network, no file reads beyond DB
- Must skip `mcp__*` tools to prevent double-counting with proxy path

Then add the agent to `_AGENT_CONFIG` in `cli_hook.py` for the install command.

## Database

### Schema (db/schema.py)

Five tables:

| Table | Purpose |
|-------|---------|
| `session` | One row per proxy run or hook session |
| `tool_call` | One row per tool invocation |
| `budget` | Budget rules (session/daily limits) |
| `breaker` | Circuit breaker configs |
| `breaker_trip` | Circuit breaker trip log |
| `rate_card` | Model pricing for cost estimation |

### Key columns on `tool_call`

| Column | Source | Purpose |
|--------|--------|---------|
| `server_name` | Agent name or MCP server name | Groups data by source |
| `agent` | Hook adapter AGENT constant | Distinguishes Claude/Gemini/Codex/Copilot |
| `project` | Extracted from cwd | Per-project analytics |
| `model_id` | Hook payload or env var | Cost estimation via rate card |
| `input_size` | len(arguments) before truncation | Cost estimation (input tokens) |
| `result_size` | len(response) | Cost estimation (output tokens) |
| `arguments_json` | Truncated to 1000 chars | Debugging, project extraction |
| `result_json` | Truncated to 2000 chars | Debugging |

### Migrations (db/schema.py:_migrate)

Additive only. Pattern:

```python
cols = {r[1] for r in conn.execute("PRAGMA table_info(tool_call)").fetchall()}
if "new_column" not in cols:
    conn.execute("ALTER TABLE tool_call ADD COLUMN new_column TEXT NOT NULL DEFAULT ''")
```

Never remove columns. Never change column types. Old data stays valid.

### WAL Mode

SQLite is configured with WAL (Write-Ahead Logging) for concurrent
read/write — the proxy or hook writes while CLI commands read. Do not
change the journal mode.

## DB Package (db/)

`MeterDB` is the single entry point. Submodules contain functions that
take `conn: sqlite3.Connection` as first arg. MeterDB delegates:

```python
class MeterDB:
    def record_call(self, call: ToolCall) -> None:
        calls.record_call(self._conn, call)
```

| Module | Responsibility |
|--------|---------------|
| `__init__.py` | MeterDB class, delegates to submodules |
| `schema.py` | DDL, migrations, rate card seeding |
| `sessions.py` | Session CRUD, auto-naming |
| `calls.py` | Tool call recording and queries |
| `budget.py` | Budget CRUD and enforcement |
| `breaker.py` | Circuit breaker CRUD and trips |
| `rates.py` | Rate card CRUD |
| `analytics.py` | Distributions, aggregates, session stats |
| `_helpers.py` | build_where() for safe query construction |

## Cost Estimation (planned — Phase 3)

Facts are stored at write time. Economics are derived at query time:

```
input_size (bytes) / chars_per_token → estimated_input_tokens
result_size (bytes) / chars_per_token → estimated_output_tokens

estimated_input_tokens × rate_card.input_per_mtok / 1_000_000 → input_cost
estimated_output_tokens × rate_card.output_per_mtok / 1_000_000 → output_cost

(input_cost + output_cost) × calibration_factor → calibrated_cost
```

The `calibration_factor` absorbs the gap between tool I/O estimates and
actual billed costs (which include reasoning tokens, context loading, etc.).
Set via `agentmeter calibrate --actual-spend <amount> --period <days>`.

## Module Size Discipline

Soft ceiling: 200-300 lines. Hard ceiling: 500 lines.

Split by domain boundary when a module grows, not by arbitrary line count.
Current largest modules:

- `proxy.py` — 456 lines (single cohesive class, not splitting yet)
- `cli.py` — 211 lines
- `db/analytics.py` — 182 lines

## Entry Points

| Command | Target | Stability |
|---------|--------|-----------|
| `python3 -m agentmeter.hook` | `hooks/claude.py` via shim | Permanent (in users' settings.json) |
| `python3 -m agentmeter.hooks.claude` | Direct | Stable |
| `python3 -m agentmeter.hooks.gemini` | Direct | Stable |
| `python3 -m agentmeter.hooks.codex` | Direct | Stable |
| `python3 -m agentmeter.hooks.copilot` | Direct | Stable |
| `agentmeter <command>` | `cli.py:main` | Stable |
