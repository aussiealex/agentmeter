# AgentMeter

**Know what your agents cost.**

AgentMeter is the universal metering layer for AI coding agents. It captures
every tool call — whether through agent hooks or an MCP proxy — and gives you
tool call analytics, budget enforcement, and cost visibility.

Works with **Claude Code**, **Gemini CLI**, **Codex CLI**, **Copilot CLI**,
and any MCP-compatible agent. Runs entirely on your machine. No cloud, no
accounts, no signup.

## Why

AI coding agents are powerful, but they're expensive and opaque. A single
session can burn through $50+ in API costs, and you won't know until the
bill arrives.

AgentMeter sits at the tool boundary (what agents *do*, not what they
*think*) and gives you visibility into every tool call, every session,
every project.

## Quick Start

```bash
pip install agent-usage
```

### 1. Install the hook (30 seconds)

```bash
agentmeter hook install claude
```

This prints a config snippet. Add it to `~/.claude/settings.json` and every
tool call is metered automatically. Same for other agents:

```bash
agentmeter hook install gemini
agentmeter hook install codex
agentmeter hook install copilot
```

### 2. Use your agent normally

Just code. AgentMeter records every tool call in the background with <5ms
overhead.

### 3. See what happened

```
$ agentmeter stats

  AgentMeter Stats (today)
  ────────────────────────────────────────────────────────────
  Total: 847 calls | 3 errors | 142ms tool time

  Read                 ████████████████████   312 calls
  Edit                 ████████████           198 calls
  Bash                 █████████              147 calls
  Grep                 ██████                  89 calls
```

```
$ agentmeter daily

  Daily Totals (last 7 days)
  ──────────────────────────────────────────────────────
  2026-05-14  ███████████          383 calls
  2026-05-15  █████████████████    718 calls
  2026-05-16  █████████████████    847 calls
  2026-05-17  ██████████████████   773 calls
  2026-05-18  █████████████████    742 calls
  2026-05-19  ██████████           340 calls
  2026-05-20  ████████████         419 calls
```

## Features

### Tool Call Stats

Track every tool call across all your agents and projects:

```bash
agentmeter stats              # today's stats
agentmeter stats --week       # this week
agentmeter stats --all        # all time
agentmeter calls              # recent individual calls
agentmeter calls --tool Bash  # filter by tool name
agentmeter sessions           # session breakdowns with outcomes
agentmeter daily              # daily totals with bar chart
```

### Budget Enforcement

Set limits and AgentMeter will block or warn when they're exceeded. Denials
return informative errors the agent can reason about — not crashes.

```bash
agentmeter budget set session 50          # max 50 calls per session
agentmeter budget set daily 200           # max 200 calls per day
agentmeter budget set daily 100 -s mail   # per-server daily limit
agentmeter budget set session 30 -a warn  # warn but don't block
agentmeter budget show                    # list all rules
```

### Circuit Breakers

Velocity-based protection against runaway loops:

```bash
agentmeter breaker set 20 60     # trip after 20 calls in 60 seconds
agentmeter breaker set 10 30 -c 600  # custom cooldown (600s)
```

### MCP Proxy

If you run MCP servers, AgentMeter can sit between your agent and the server
as a transparent proxy — metering every MCP tool call without changing either
side:

```bash
agentmeter wrap python -m some.mcp.server
agentmeter wrap --name myserver python -m some.mcp.server
```

In your agent's `.mcp.json`, just prefix the command:

```json
{
  "mcpServers": {
    "myserver": {
      "command": "agentmeter",
      "args": ["wrap", "--name", "myserver", "python", "-m", "some.mcp.server"]
    }
  }
}
```

Hook data and proxy data feed the same database — built-in tools and MCP
tools in one view.

### Rate Card

View and customise per-model pricing for cost estimation:

```bash
agentmeter rates              # view all rates
agentmeter rates set <model>  # edit a model's rates
```

## How It Works

Two capture paths feed the same SQLite database:

```
Path 1: Hook (primary — works with any agent that has a hook system)
  Agent's built-in tools -> PostToolUse hook -> agentmeter -> SQLite DB

Path 2: MCP Proxy (for metering MCP server traffic)
  Agent -> AgentMeter proxy -> MCP Server -> SQLite DB
```

The hook path is the primary product. It captures built-in tool calls (Read,
Edit, Bash, etc.) with zero config changes to your agent. The MCP proxy is
the advanced path for metering MCP server traffic.

### Architecture

- **Local-first** — SQLite with WAL mode, works offline, no cloud dependency
- **Transparent** — never modifies tool call data, just observes
- **Cross-platform** — Linux, macOS, Windows
- **Fast hooks** — <5ms overhead, stdlib only, never crashes the agent

## Supported Agents

| Agent | Hook Type | Status |
|-------|-----------|--------|
| Claude Code | PostToolUse | Full support |
| Gemini CLI | AfterTool | Full support |
| Codex CLI | PostToolUse | Full support |
| Copilot CLI | postToolUse | Full support |

## AgentMeter Pro

For teams and power users who need deeper cost intelligence:

- **Real token cost analysis** — actual API costs from session transcripts
- **Web dashboard** — visual overview of spend across projects and sessions
- **Spend forecasting** — projected monthly costs based on usage patterns
- **Strategy recommendations** — actionable advice to reduce agent spend
- **Data export** — JSONL export for external analysis

Contact: [coming soon]

## CLI Reference

| Command | Description |
|---------|-------------|
| `agentmeter stats` | Tool call stats (today, `--week`, `--all`) |
| `agentmeter calls` | Recent individual calls |
| `agentmeter sessions` | Session breakdowns with outcomes |
| `agentmeter daily` | Daily totals with bar chart |
| `agentmeter budget` | Budget rules (set/show/clear) |
| `agentmeter breaker` | Circuit breakers (set/show/clear) |
| `agentmeter hook` | Hook management (install/status) |
| `agentmeter rates` | View/edit rate card |
| `agentmeter wrap` | MCP proxy mode |
| `agentmeter rename` | Rename a session |
| `agentmeter backfill` | Detect outcomes in historical sessions |

## Requirements

- Python 3.11+
- No external services or API keys

## Licence

Apache 2.0 — see [LICENSE](LICENSE).
