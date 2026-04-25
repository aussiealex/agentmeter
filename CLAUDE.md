# CLAUDE.md — AgentMeter

## What This Is

AgentMeter is an open-source MCP proxy that meters every tool call between an
AI agent (Claude Code, Cursor, etc.) and any MCP server. It's the missing
economics layer for MCP agents.

**Tagline:** "Know what your agents cost."

## Architecture

```
Agent (Claude Code) → AgentMeter (proxy) → MCP Server (any)
                          ↓
                     SQLite DB
                     (~/.local/share/agentmeter/agentmeter.db)
```

The proxy is transparent — the agent doesn't know it's there, the MCP server
doesn't know it's there. AgentMeter spawns the child MCP server as a subprocess
and proxies all MCP traffic via stdio.

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
├── __init__.py       # Version
├── __main__.py       # python -m agentmeter entry point
├── proxy.py          # MCP proxy core — the main product
├── db.py             # SQLite storage for metering data
├── models.py         # Dataclasses: ToolCall, Session, ToolStats, SessionStats
└── cli.py            # CLI: wrap, stats, sessions, calls, daily, rename
tests/
├── conftest.py       # Shared fixtures (tmp_db, test_server_path)
├── test_db.py        # DB unit tests (positive)
├── test_security.py  # SQL injection, file system safety, data truncation
├── test_boundaries.py # String, numeric, limit, time edge cases
├── test_cli.py       # CLI command tests via CliRunner
├── test_integration.py # End-to-end proxy tests (pytest)
├── test_proxy.py     # Standalone integration test (manual)
├── test_server.py    # Minimal MCP server for testing
└── TEST_STRATEGY.md  # Test strategy with deferred test triggers
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

Check `/media/aa/LargeBackup/MainApps/_playbook/actionables.md` for
digest-sourced action items tagged to this project.

## Constraints

- ruff must pass with zero errors before any commit
- All data structures are dataclasses — no dicts-as-data
- Proxy must be fully transparent — no modification of tool call data
- Local-first: no cloud services, no accounts, no signup
- All SQL queries must use parameterized `?` placeholders — no f-string interpolation
