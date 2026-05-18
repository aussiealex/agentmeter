# CLAUDE.md — AgentMeter

## What This Is

AgentMeter is the universal metering layer for AI coding agents. It captures
every tool call — whether through an MCP proxy or agent hook system — and
provides cost attribution, spend forecasting, and budget enforcement.

It works with Claude Code, Gemini CLI, Codex CLI, Copilot CLI, and any
MCP-compatible agent.

**Tagline:** "Know what your agents cost."

## Business Role: Long-Term Bet (Priority #3)

At session start, read `/media/aa/LargeBackup/MainApps/_playbook/session-start.md`
and follow its instructions. This project is Priority #3 — only after revenue
and beta test are unblocked.

Read `/media/aa/LargeBackup/MainApps/_playbook/business-thesis.md` for full
strategy context.

## Architecture

Two capture paths feed the same database:

```
Path 1: Hook (primary — every Claude Code / Gemini / Codex / Copilot user)
  Agent's built-in tools → PostToolUse hook → agentmeter.hooks.<agent> → SQLite DB

Path 2: MCP Proxy (power users running MCP servers)
  Agent → AgentMeter proxy → MCP Server (child subprocess) → SQLite DB

Both paths → same DB (~/.local/share/agentmeter/agentmeter.db)
           → same CLI commands (stats, sessions, daily, advise, forecast)
           → same rate card for cost estimation
```

The hook path is the primary product — it captures built-in tool calls (Read,
Edit, Bash, etc.) for any agent with a hook system. The MCP proxy is the
advanced path for metering MCP server traffic.

## Tech Stack

- **Language:** Python 3.11+
- **MCP:** mcp SDK
- **Database:** SQLite (WAL mode)
- **CLI:** click
- **Async:** anyio
- **Lint:** ruff

## Project Structure

```
src/agentmeter/
├── __init__.py          # Version
├── __main__.py          # python -m agentmeter entry point
├── proxy.py             # MCP proxy core (Path 2)
├── models.py            # Dataclasses: ToolCall, Session, NormalisedToolEvent, etc.
├── cli.py               # CLI: wrap, stats, sessions, calls, daily, rename, rates
├── hook.py              # Legacy entry point (imports from hooks/claude.py)
├── hooks/               # Multi-agent hook system (Path 1)
│   ├── __init__.py      # Re-exports, backwards compat
│   ├── base.py          # NormalisedToolEvent → DB recording logic
│   ├── claude.py        # Claude Code PostToolUse adapter
│   ├── gemini.py        # Gemini CLI AfterTool adapter
│   ├── codex.py         # Codex CLI PostToolUse adapter
│   └── copilot.py       # Copilot CLI postToolUse adapter
└── db/                  # Database layer (split by domain)
    ├── __init__.py      # MeterDB class, re-exports
    ├── schema.py        # Schema DDL, migrations, connection setup
    ├── sessions.py      # Session CRUD + auto-naming
    ├── calls.py         # Tool call recording + queries
    ├── budget.py        # Budget CRUD + checking
    ├── breaker.py       # Circuit breaker CRUD + trips
    ├── rates.py         # Rate card CRUD
    └── analytics.py     # Distribution, aggregates, cost estimation
tests/
├── conftest.py          # Shared fixtures (tmp_db, test_server_path)
├── test_db.py           # DB unit tests (positive)
├── test_security.py     # SQL injection, file system safety, data truncation
├── test_boundaries.py   # String, numeric, limit, time edge cases
├── test_cli.py          # CLI command tests via CliRunner
├── test_hooks.py        # Hook adapter tests (all agents)
├── test_integration.py  # End-to-end proxy tests (pytest)
├── test_proxy.py        # Standalone integration test (manual)
├── test_server.py       # Minimal MCP server for testing
└── TEST_STRATEGY.md     # Test strategy with deferred test triggers
```

## Running

```bash
# Install
pip install -e .

# Wrap any MCP server
agentmeter wrap python -m some.mcp.server
agentmeter wrap --name myserver python -m some.mcp.server

# View stats
agentmeter stats              # today
agentmeter stats --all        # all time
agentmeter stats --week       # this week
agentmeter calls              # recent individual calls
agentmeter calls --tool add   # filter by tool name
agentmeter sessions           # session breakdowns
agentmeter daily              # daily totals with bar chart

# Budget enforcement
agentmeter budget set session 50          # max 50 calls per session
agentmeter budget set daily 200           # max 200 calls per day
agentmeter budget set daily 100 -s mail   # per-server daily limit
agentmeter budget set session 30 -a warn  # warn but don't block
agentmeter budget show                    # list all rules
agentmeter budget clear --yes             # remove all rules

# Circuit breakers (velocity-based)
agentmeter breaker set 20 60              # trip after 20 calls in 60s
agentmeter breaker set 10 30 -c 600       # custom cooldown (600s)
agentmeter breaker set 50 120 -s mailsift # server-specific
agentmeter breaker show                   # configs + recent trips
agentmeter breaker clear --yes            # remove all

# Run tests
python3 -m pytest tests/ -v
```

## Key Design Decisions

- **MCP-native:** Sits at the tool boundary (what agents DO), not the model
  boundary (what agents think). This is the differentiator from LangSmith,
  Arize, Langfuse, etc.
- **Local-first:** SQLite, no cloud dependency, works offline
- **Open source first:** Free gets distribution, paid comes from hosted/enterprise
- **Transparent proxy:** Zero config changes needed on either side — just wrap
  the command
- **Budget-aware denials:** When a budget is exceeded, the proxy returns an
  informative error the agent can reason about — not a crash or silent drop.
  Warn mode logs but allows the call through.

## Non-Obvious Patterns

Patterns that cause subtle bugs if an agent doesn't know about them:

- **WAL mode is critical** — SQLite is configured with WAL (Write-Ahead Logging) for concurrent read/write between the proxy and the CLI stats commands. Don't change the journal mode or add `PRAGMA journal_mode=DELETE` — it will cause locking errors during active metering.
- **Proxy must never modify tool call data** — AgentMeter is a transparent proxy. It reads and records tool calls but must never alter the request or response payloads. Any change to the proxy path must preserve this invariance.
- **Session boundaries are inferred** — there's no explicit "session start" signal from the agent. Sessions are inferred from gaps in tool call timestamps. If you change the gap threshold, existing session groupings in historical data will shift.
- **Empty README** — the README has no content yet. This is a known gap for the open-source launch but don't generate one without the user's input — it's a marketing asset, not just docs.

## Pending Enhancements

At session start, read `/media/aa/LargeBackup/MainApps/_playbook/actionables-agentmeter.md`
and briefly list pending items (count + top 3).
Don't block on this — just surface them so the user knows what's queued.

## Session Handoff

If `.handoff.md` exists in the project root, read it before doing anything
else — it contains context from a prior session's unfinished work. When
ending a session mid-task, write `.handoff.md` following the protocol in
`/media/aa/LargeBackup/MainApps/_playbook/session-handoff-protocol.md`.
Delete it when the task is complete.

## Codebase Principles

### Hard constraints
- ruff must pass with zero errors before any commit
- All data structures are dataclasses — no dicts-as-data
- Proxy must be fully transparent — no modification of tool call data
- Local-first: no cloud services, no accounts, no signup
- All SQL queries must use parameterised `?` placeholders — no f-string interpolation

### Data integrity
- Metering data is the product. Never silently drop data — log to stderr on failure.
- Every write path must handle crashes gracefully (WAL mode, atomic commits).
- Store facts at write time, derive economics at query time. Never store computed
  costs — they go stale when rates change.

### Module discipline
- Each `.py` file has one clear responsibility. Soft ceiling ~200-300 lines,
  hard ceiling 500. Split by domain boundary, not arbitrary line count.
- Layers: Hooks (capture) → DB (storage) → Queries (analysis) → CLI (display).
  Each layer has one job. Don't cross boundaries.

### Hook path rules
- Hooks are a hot path (<5ms). Minimal imports, no network calls, no file reads
  beyond the DB. stdlib + agentmeter.db only.
- If a hook fails, exit cleanly. The agent's work is more important than metering.
- No stdout pollution — agents parse stdout. Diagnostics go to stderr only.

### Backwards compatibility
- `python3 -m agentmeter.hook` must keep working forever (installed in users'
  settings.json). New entry points are additive.
- DB schema changes are additive only — new columns with defaults or nullable.
  Old queries keep working. Old data stays valid.
- CLI commands are stable — `agentmeter stats` today must still work after changes.

### Multi-agent design
- Agent adapters are thin (~40 lines) — just field mapping to NormalisedToolEvent.
- All shared logic lives in hooks/base.py. No duplication across adapters.
- The DB schema and rate card are agent-agnostic. The `agent` column distinguishes
  data sources, but all queries work across agents by default.

### Testing
- Test the contract, not the implementation. Hook writes correct row? Pass.
  Query returns correct numbers? Pass.
- Integration tests > unit tests for a tool this size.
- Run `python3 -m pytest tests/ -v` after every change. Don't break existing tests.

### Avoid
- No premature abstraction — shared functions before base classes.
- No speculative features — build what's needed now, not what might be needed.
- No config wizards — zero-config by default, configurable when needed.
