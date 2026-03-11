# CLAUDE.md — AgentMeter

## What This Is

AgentMeter is an open-source MCP proxy that meters every tool call between an
AI agent (Claude Code, Cursor, etc.) and any MCP server. It's the missing
economics layer for MCP agents.

**Tagline:** "Know what your agents cost."

## Current Status

**Week 1 MVP — working prototype.** The proxy wraps any MCP server, forwards
all tool calls, and records metrics to SQLite. CLI shows stats, sessions,
individual calls, and daily totals. Tested against a real MCP server (MailSift)
with ProtonMail Bridge.

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
- **MCP:** mcp SDK (same as MailSift)
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
└── cli.py            # CLI: wrap, stats, sessions, calls, daily
tests/
├── test_proxy.py     # Integration test (proxy + test server)
└── test_server.py    # Minimal MCP server for testing
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

# Run integration test
python tests/test_proxy.py
```

## What's Done (Week 1)

- [x] MCP proxy that forwards all tool calls to child server
- [x] SQLite metering: tool name, timing, response size, error status
- [x] Session tracking with per-tool breakdowns
- [x] CLI with stats, calls, sessions, daily commands
- [x] Tested with real MCP server (MailSift + ProtonMail Bridge)
- [x] AGENTMETER_DB env var for custom DB path
- [x] ruff clean, zero lint errors

## What's Next (Week 1 remaining)

- [ ] Error tracking test (verify failed tool calls are recorded correctly)
- [ ] Budget enforcement (set max cost per session, refuse calls when exceeded)
- [ ] Session naming (human-readable names instead of hex IDs)
- [ ] More robust error handling in proxy

## What's Next (Week 2)

- [ ] Local web dashboard (single HTML file served by proxy)
- [ ] Cost-per-task view
- [ ] Tool leaderboard
- [ ] Trend charts
- [ ] JSON/CSV export
- [ ] Session replay view
- [ ] GitHub repo setup + public launch

## What's Next (Month 2+)

- [ ] API keys with budgets for customers
- [ ] Usage reports per customer
- [ ] Webhook on budget events
- [ ] Cost allocation rules
- [ ] Stripe integration

## Key Design Decisions

- **MCP-native:** Sits at the tool boundary (what agents DO), not the model
  boundary (what agents think). This is the differentiator from LangSmith,
  Arize, Langfuse, etc.
- **Local-first:** SQLite, no cloud dependency, works offline
- **Open source first:** Free gets distribution, paid comes from hosted/enterprise
- **Transparent proxy:** Zero config changes needed on either side — just wrap
  the command

## Related Project

AgentMeter was built alongside MailSift (/media/aa/LargeBackup/MainApps/MailSift),
which is the MCP server used for testing. MailSift connects Claude Code to
ProtonMail Bridge for email search.

## Strategy Document

Full business strategy is at:
`/media/aa/LargeBackup/MainApps/MailSift/reports/agent-economics-strategy.html`

## Constraints

- ruff must pass with zero errors before any commit
- All data structures are dataclasses — no dicts-as-data
- Proxy must be fully transparent — no modification of tool call data
- Local-first: no cloud services, no accounts, no signup
