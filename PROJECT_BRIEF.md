# AgentMeter — Project Brief

> Last updated: 2026-05-18

## What This Is

AgentMeter is a universal metering layer for AI coding agents. It captures
every tool call an agent makes — file reads, edits, shell commands, web
fetches, MCP server calls — and gives you cost attribution, spend analytics,
and budget enforcement. It works locally, stores everything in SQLite, and
requires no cloud services or accounts.

**One-liner:** "Anthropic tells you how much you spent. AgentMeter tells you
where it went."

## The Problem

AI coding agents (Claude Code, Gemini CLI, Codex, Copilot) burn tokens at
scale. Real-world costs: $500/day/developer, $1,400 single sessions, 15x
increases in 6 months. Your billing page shows one number. You can't see:

- Which project consumed the most
- Which tools are expensive vs. cheap
- Whether a session was productive or wasteful
- How to forecast next month's spend
- When an agent is spiralling (100 calls in 60 seconds)

AgentMeter captures the raw facts. The economics are derived from those facts
using a configurable rate card, so your cost estimates stay accurate as
model pricing changes.

## What Works Today

### Capture (two paths into the same database)

- **Hook path (primary)** — PostToolUse hooks that meter built-in agent tools.
  Adapters for four agents:
  - Claude Code (`agentmeter hook install claude`)
  - Gemini CLI (`agentmeter hook install gemini`)
  - OpenAI Codex CLI (`agentmeter hook install codex`)
  - GitHub Copilot CLI (`agentmeter hook install copilot`)
- **MCP proxy path** — transparent proxy that wraps any MCP server and meters
  traffic. Works with any MCP-compatible agent including Cursor, Cline, Goose.

### Analytics

- `agentmeter stats` — tool call volume, errors, timing (today/week/all-time)
- `agentmeter sessions` — per-session breakdowns with tool leaderboards
- `agentmeter daily` — daily totals with bar chart
- `agentmeter calls` — individual call log with filtering
- `agentmeter stats --distribution` — p50/p90/p99 session metrics per server

### Enforcement

- **Budgets** — session or daily call limits, deny or warn mode
- **Circuit breakers** — velocity-based gating (e.g. trip after 20 calls in 60s)
- Both return informative errors the agent can reason about, not crashes

### Foundation (just built)

- **Rate card** — pricing table for 9 models (Anthropic, Google, OpenAI),
  configurable per-model with calibration factor
- **Multi-agent schema** — `agent`, `project`, `model_id`, `input_size` columns
  on every tool call record
- **Normalised event model** — all hook adapters produce the same dataclass,
  all downstream features consume it uniformly

## Supported Agents

| Agent | Capture Method | Model ID | Install |
|-------|---------------|----------|---------|
| Claude Code | PostToolUse hook | From env var | `agentmeter hook install claude` |
| Gemini CLI | AfterTool hook | From AfterModel event | `agentmeter hook install gemini` |
| Codex CLI | PostToolUse hook | In payload | `agentmeter hook install codex` |
| Copilot CLI | postToolUse hook | Not yet available | `agentmeter hook install copilot` |
| Any MCP client | MCP proxy | N/A | `agentmeter wrap <command>` |

## Architecture

```
Capture layer:
  Claude Code ──→ hooks/claude.py ──→ NormalisedToolEvent ──┐
  Gemini CLI  ──→ hooks/gemini.py ──→ NormalisedToolEvent ──┤
  Codex CLI   ──→ hooks/codex.py  ──→ NormalisedToolEvent ──┼──→ hooks/base.py
  Copilot CLI ──→ hooks/copilot.py ─→ NormalisedToolEvent ──┤     record_event()
  MCP client  ──→ proxy.py ─────────────────────────────────┘         │
                                                                      ▼
Storage layer:                                              SQLite DB (WAL mode)
  db/schema.py ── tables: session, tool_call, budget, breaker, rate_card
  db/sessions.py, db/calls.py, db/budget.py, db/breaker.py, db/rates.py

Query layer:                                                db/analytics.py
  Stats, distributions, daily totals, session breakdowns

Display layer:                                              cli.py + cli_*.py
  agentmeter stats | sessions | daily | calls | budget | breaker | hook
```

All paths write to the same DB (`~/.local/share/agentmeter/agentmeter.db`).
All CLI commands read from it. The hook and proxy paths are independent —
use one or both.

## What's Next

### Phase 3 — Economics Layer (next to build)

The rate card and schema are in place. These features consume them:

- **Cost estimation module** — `(input_size + result_size) / chars_per_token * rate`
  computed at query time, not stored
- **`agentmeter rates`** — view and update the rate card from CLI
- **`agentmeter calibrate`** — feed in actual Anthropic spend for a period,
  auto-compute calibration factor so estimates match reality
- **`agentmeter advise`** — credit pool advisor: ranks tools by estimated cost,
  flags expensive patterns, recommends optimisations
- **`agentmeter forecast`** — project monthly spend from current daily velocity
- **`agentmeter strategy`** — per-project spend vs. business priority alignment

### Phase 4 — Surface

- **README** — the marketing front door (empty today, intentionally deferred)
- **Web dashboard** — `agentmeter dashboard`, localhost single-page app
- **JSONL export** — `agentmeter export --jsonl` for external tooling

## Key Decisions

**Store facts, derive economics.** Raw measurements (sizes, model ID, timestamps)
go in the DB at write time. Cost estimates are computed at query time from facts
plus the rate card. This means changing model prices doesn't require data
migration — just update the rate card.

**Hook path is the primary product.** The MCP proxy was the original feature,
but most agent users never touch MCP. The hook path captures built-in tools
for every Claude Code / Gemini / Codex / Copilot user. The proxy is the
advanced path.

**Thin adapters, shared core.** Each agent hook adapter is ~80 lines of field
mapping. All shared logic (DB writes, session management, project extraction)
lives in `hooks/base.py`. Adding a new agent is a single file.

**Additive schema only.** New columns have defaults or are nullable. Old data
stays valid. Old queries keep working. The migration path is always forward.

**The agent's work matters more than metering.** Hooks must be <5ms, never
crash, never write to stdout. If the hook fails, it exits cleanly and the
agent continues uninterrupted.

**Budget denials are informative, not destructive.** When a budget or breaker
trips, the agent receives a text explanation it can reason about. No crashes,
no silent drops.

## Tech Stack

- Python 3.11+
- SQLite (WAL mode) — local-first, no cloud
- click — CLI framework
- anyio — async for MCP proxy
- mcp SDK — MCP protocol handling
- ruff — linting (zero errors required)

## Project Structure

```
src/agentmeter/
├── hooks/           # Multi-agent hook adapters (capture layer)
│   ├── base.py      # Shared: NormalisedToolEvent → DB
│   ├── claude.py    # Claude Code PostToolUse
│   ├── gemini.py    # Gemini CLI AfterTool
│   ├── codex.py     # Codex CLI PostToolUse
│   └── copilot.py   # Copilot CLI postToolUse
├── db/              # Database layer (storage + queries)
│   ├── schema.py    # DDL, migrations, rate card seeding
│   ├── sessions.py  # Session CRUD + auto-naming
│   ├── calls.py     # Tool call recording + queries
│   ├── budget.py    # Budget enforcement
│   ├── breaker.py   # Circuit breakers
│   ├── rates.py     # Rate card CRUD
│   └── analytics.py # Distributions, aggregates
├── cli.py           # Core CLI commands
├── cli_budget.py    # Budget CLI subgroup
├── cli_breaker.py   # Breaker CLI subgroup
├── cli_hook.py      # Hook install/status CLI
├── cli_format.py    # Output formatting helpers
├── proxy.py         # MCP transparent proxy
├── models.py        # All dataclasses
└── hook.py          # Backwards-compat shim
tests/               # 225 tests (pytest)
```

## Running

```bash
pip install -e .

# Install hook for your agent
agentmeter hook install claude    # or gemini, codex, copilot

# Or wrap an MCP server
agentmeter wrap python -m some.mcp.server

# View your data
agentmeter stats
agentmeter sessions
agentmeter daily
agentmeter calls --tool Bash

# Run tests
python3 -m pytest tests/ -v
```
