# AgentMeter Constitution

> Single source of truth. Read before every session. Never violate these rules.

## Version
1.0.0

---

## The Hook

**AgentMeter is the universal metering layer for AI coding agents.** It captures
every tool call an agent makes — file reads, edits, shell commands, web fetches,
MCP server calls — and gives you cost attribution, spend analytics, and budget
enforcement.

Two capture paths feed the same database:
- **Hook path (primary)** — PostToolUse hooks for Claude Code, Gemini CLI,
  Codex CLI, Copilot CLI. Captures built-in tools.
- **MCP proxy path** — transparent proxy wrapping any MCP server. Captures
  MCP tool calls for any agent.

**Slogan:** "Anthropic tells you how much you spent. AgentMeter tells you where
it went."

---

## Core Principles

### I. Data Integrity Above All Else

Metering data is the product. A lost or corrupted row is worse than a slow write.
- Every write path must handle crashes gracefully (WAL mode, atomic commits)
- Never silently drop data — log to stderr on failure
- If a hook fails, exit cleanly. The agent's work is more important than metering.

### II. Store Facts, Derive Economics

Raw measurements (sizes, model ID, timestamps, token counts) go in the DB at
write time. Cost estimates are computed at query time from facts plus the rate
card. Never store computed costs — they go stale when rates change.

### III. Backwards Compatibility Is Permanent

- `python3 -m agentmeter.hook` must keep working forever (installed in users'
  settings.json). New entry points are additive.
- DB schema changes are additive only — new columns with defaults or nullable.
  Old queries keep working. Old data stays valid.
- CLI commands are stable — `agentmeter stats` today must still work after changes.

### IV. Zero-Config by Default

`agentmeter hook install claude` should just work. No YAML files, no env vars
to set, no config wizard. Sensible defaults everywhere. Configuration is an
escape hatch, not a requirement.

### V. Thin Adapters, Shared Core

Each agent hook adapter is ~80 lines of field mapping. All shared logic lives in
`hooks/base.py`. Adding a new agent is a single file. No duplication across adapters.

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | Python 3.11+ | |
| Database | SQLite (WAL mode) | Local-first, no cloud |
| CLI | click | |
| Async | anyio | MCP proxy only |
| MCP | mcp SDK | MCP protocol handling |
| Lint | ruff | Zero errors required |
| Tests | pytest | |

---

## Architecture Rules (Non-Negotiable)

### 1. Layer Boundaries

```
CAPTURE    hooks/<agent>.py, proxy.py     → NormalisedToolEvent / ToolCall
STORAGE    db/                            → SQLite (one DB, many writers)
QUERY      db/analytics.py, db/calls.py   → Dataclasses
DISPLAY    cli.py, cli_*.py               → Formatted output
```

Layer violations are never acceptable:
- Capture layer never formats output
- Query layer never writes data
- Display layer never touches SQLite directly
- New agents only touch the capture layer
- New analytics only touch the query layer
- New commands only touch the display layer

### 2. Directory Structure

```
src/agentmeter/
├── hooks/               # Multi-agent hook adapters (capture layer)
│   ├── __init__.py      # Re-exports, entry point docs
│   ├── base.py          # Shared: NormalisedToolEvent → DB recording
│   ├── claude.py        # Claude Code PostToolUse
│   ├── gemini.py        # Gemini CLI AfterTool
│   ├── codex.py         # Codex CLI PostToolUse
│   └── copilot.py       # Copilot CLI postToolUse
├── db/                  # Database layer (storage + queries)
│   ├── __init__.py      # MeterDB class, delegates to submodules
│   ├── schema.py        # DDL, migrations, rate card seeding
│   ├── sessions.py      # Session CRUD + auto-naming
│   ├── calls.py         # Tool call recording + queries
│   ├── budget.py        # Budget enforcement
│   ├── breaker.py       # Circuit breakers
│   ├── rates.py         # Rate card CRUD
│   ├── analytics.py     # Distributions, aggregates
│   └── _helpers.py      # build_where() for safe query construction
├── session_reader.py    # Read real tokens from agent session files
├── cli.py               # Core CLI commands
├── cli_budget.py        # Budget CLI subgroup
├── cli_breaker.py       # Breaker CLI subgroup
├── cli_hook.py          # Hook install/status CLI
├── cli_format.py        # Output formatting helpers
├── proxy.py             # MCP transparent proxy
├── models.py            # All dataclasses
└── hook.py              # Backwards-compat shim → hooks/claude.py
specs/                   # Design specs (written before implementation)
tests/                   # pytest suite
```

### 3. Module Size Discipline

- Each `.py` file has one clear responsibility
- Soft ceiling: 200-300 lines. Hard ceiling: 500 lines.
- Split by domain boundary, not arbitrary line count
- If you can't describe what a file does in one sentence, it's doing too much

### 4. Database Patterns

- **WAL mode is critical** — concurrent read/write between proxy/hooks and CLI.
  Never change the journal mode.
- **Parameterised SQL only** — all queries use `?` placeholders. No f-string
  interpolation. No exceptions.
- **Additive migrations only** — check column existence with `PRAGMA table_info`,
  add with ALTER TABLE. Never drop, never rename.
- **One connection, one class** — `MeterDB` owns the connection. Submodules take
  `conn: sqlite3.Connection` as first arg.
- **Commit immediately** — no long-lived transactions. Write and commit.

### 5. Hook Path Rules

Hooks are a hot path — they fire on every tool call in every agent session.

- **<5ms total overhead** — minimal imports, no network, no file reads beyond DB
- **stdlib + agentmeter.db only** — no heavy third-party imports
- **Never write to stdout** — agents parse stdout. Diagnostics go to stderr only.
- **Never raise** — catch all exceptions, log to stderr, exit 0
- **Skip `mcp__*` tools** — prevents double-counting with proxy path

### 6. Data Structures

- **Dataclasses for all data** — no dicts-as-data. Every return type from the DB
  layer is a dataclass defined in `models.py`.
- **NormalisedToolEvent** — the contract between hook adapters and the storage layer.
  All adapters produce this, all downstream features consume it.
- **RateCard** — model pricing for query-time cost estimation. Never hardcode rates.

---

## Database Schema

### Core Tables

**`session`** — one row per proxy run or hook session
```
id              TEXT PRIMARY KEY
name            TEXT NOT NULL DEFAULT ''
server_name     TEXT NOT NULL
server_command  TEXT NOT NULL
started_at      TEXT NOT NULL
ended_at        TEXT
total_calls     INTEGER NOT NULL DEFAULT 0
```

**`tool_call`** — one row per tool invocation
```
id              INTEGER PRIMARY KEY AUTOINCREMENT
session_id      TEXT NOT NULL REFERENCES session(id)
server_name     TEXT NOT NULL
tool_name       TEXT NOT NULL
arguments_json  TEXT NOT NULL DEFAULT '' (truncated to 1000 chars)
result_json     TEXT NOT NULL DEFAULT '' (truncated to 2000 chars)
result_size     INTEGER NOT NULL DEFAULT 0
is_error        INTEGER NOT NULL DEFAULT 0
started_at      TEXT NOT NULL
elapsed_ms      INTEGER NOT NULL DEFAULT 0
created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
agent           TEXT NOT NULL DEFAULT ''
project         TEXT NOT NULL DEFAULT ''
model_id        TEXT NOT NULL DEFAULT ''
input_size      INTEGER NOT NULL DEFAULT 0
```

**`rate_card`** — model pricing for cost estimation
```
model_id           TEXT PRIMARY KEY
display_name       TEXT NOT NULL DEFAULT ''
input_per_mtok     REAL NOT NULL
output_per_mtok    REAL NOT NULL
cached_per_mtok    REAL NOT NULL DEFAULT 0
chars_per_token    REAL NOT NULL DEFAULT 4.0
calibration_factor REAL NOT NULL DEFAULT 1.0
updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
```

**`budget`** — session or daily call limits
```
id, scope, server_name, max_calls, action, created_at
```

**`breaker`** / **`breaker_trip`** — velocity-based circuit breakers

---

## Supported Agents

| Agent | Hook Event | Module | Server Name |
|-------|-----------|--------|-------------|
| Claude Code | PostToolUse | hooks/claude.py | `claude-code` |
| Gemini CLI | AfterTool | hooks/gemini.py | `gemini-cli` |
| Codex CLI | PostToolUse | hooks/codex.py | `codex-cli` |
| Copilot CLI | postToolUse | hooks/copilot.py | `copilot-cli` |
| MCP (any) | Proxy | proxy.py | Inferred from command |

### Adding a New Agent

1. Create `hooks/<agent>.py` — read stdin JSON, normalise to `NormalisedToolEvent`,
   call `record_event()`. ~80 lines.
2. Add entry to `_AGENT_CONFIG` in `cli_hook.py` for the install command.
3. Add `__main__` block so `python3 -m agentmeter.hooks.<agent>` works.
4. Add tests in `tests/test_hooks.py`.
5. Update this constitution's agent table.

---

## Cost Model

### Token Sources (best to worst)

1. **Real tokens** — read from agent session files on disk (Claude Code JSONL).
   Exact Anthropic API usage data. See `specs/session-token-reader.md`.
2. **Byte estimation** — `(input_size + result_size) / chars_per_token * rate`.
   Directionally useful but inaccurate. Fallback when real data unavailable.

### Rate Card Calculation

```
input_cost        = input_tokens × input_per_mtok / 1,000,000
cache_create_cost = cache_creation_tokens × input_per_mtok / 1,000,000
cache_read_cost   = cache_read_tokens × cached_per_mtok / 1,000,000
output_cost       = output_tokens × output_per_mtok / 1,000,000
total_cost        = sum of above × calibration_factor
```

Cache reads typically dominate (80-90% of total tokens) because conversation
history, system prompt, and project context are re-sent every turn.

---

## What NOT to Build

| Feature | Reason |
|---------|--------|
| Cloud service or account system | Local-first. No signup. No cloud dependency. |
| Web scraping of billing pages | Fragile, ToS violation. Read local files instead. |
| Real-time streaming dashboard | Complexity. Batch CLI queries are sufficient for now. |
| Agent modification or control | AgentMeter observes. It never modifies tool call data. |
| ML-based cost prediction | Simple extrapolation first. ML only if proven insufficient. |
| Multi-user or team features | Solo tool first. Team features are the paid tier (future). |

---

## Non-Obvious Patterns

Patterns that cause subtle bugs if an agent doesn't know about them:

- **WAL mode is critical** — SQLite WAL enables concurrent read/write. Don't add
  `PRAGMA journal_mode=DELETE` — it causes locking errors during active metering.
- **Proxy must never modify tool call data** — AgentMeter is transparent. It reads
  and records but must never alter request or response payloads.
- **Session boundaries are inferred** — no explicit "session start" signal from
  most agents. Sessions are inferred from gaps in timestamps or from hook session IDs.
- **`hook.py` is a permanent shim** — users have `python3 -m agentmeter.hook` in
  their settings.json. This entry point must always work, even though the real
  code lives in `hooks/claude.py`.
- **Budget denials are informative** — when a budget or breaker trips, the agent
  receives a text explanation it can reason about. Not a crash, not a silent drop.
- **Claude Code JSONL path** — session transcripts live at
  `~/.claude/projects/<slug>/<session-id>.jsonl` where slug is the cwd with `/`
  replaced by `-`. This is an internal format that could change without notice.

---

## Validation Commands

Run these before declaring any work done:

```bash
python3 -m ruff check src/ tests/      # Zero errors required
python3 -m pytest tests/ -v             # All tests must pass
```

---

## Failure Modes to Actively Avoid

- "Storing computed costs in the DB" → NO, derive at query time from rate card
- "Adding a heavy import to a hook adapter" → NO, hooks must be <5ms
- "Writing to stdout from a hook" → NO, agents parse stdout, use stderr
- "Dropping a DB column in a migration" → NO, additive only
- "Breaking `python3 -m agentmeter.hook`" → NO, permanent backwards compat
- "Using f-strings in SQL queries" → NO, parameterised `?` only
- "Returning dicts from DB methods" → NO, dataclasses only
- "Building a module over 500 lines" → NO, split by domain boundary
- "Modifying tool call data in the proxy" → NO, transparent observation only
- "Estimating cost when real token data is available" → NO, use the real data

---

## The Acid Test

Before implementing any feature, ask:

1. Does this help someone understand what their agents cost?
2. Does it work with zero configuration out of the box?
3. Does it preserve backwards compatibility with existing data and entry points?
4. Is it fast enough to run on every tool call without the user noticing?

---

## Governance

- **Amendments**: Update this file, increment version, note changes below
- **Compliance**: Follow principles in spirit, not just letter
- **Exceptions**: Document and justify when deviating

---

**Created**: 2026-05-18
**Version**: 1.0.0
