# AgentMeter

**Know what your agents cost.**

AgentMeter is the universal metering layer for AI coding agents. It captures
every tool call and API interaction — whether through agent hooks or an MCP
proxy — and gives you real cost attribution, spend forecasting, and budget
enforcement.

Works with **Claude Code**, **Gemini CLI**, **Codex CLI**, **Copilot CLI**,
and any MCP-compatible agent. Runs entirely on your machine. No cloud, no
accounts, no signup.

## Why

AI coding agents are powerful, but they're expensive and opaque. A single
session can burn through $50+ in API costs, and you won't know until the
bill arrives. Costs scale quadratically — every turn replays the entire
conversation as input.

AgentMeter sits at the tool boundary (what agents *do*, not what they
*think*) and answers:

- How much did that session cost?
- Which project is eating my budget?
- Should I split this session or keep going?
- Am I on track for my monthly spend target?

## Quick Start

```bash
pip install agentmeter
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

### 3. See what it cost

```
$ agentmeter daily

  Daily Totals (last 7 days)
  ──────────────────────────────────────────────────────
  2026-05-14  ███████████          383 calls  $  277.23
  2026-05-15  █████████████████    718 calls
  2026-05-16  █████████████████    847 calls  $  320.07
  2026-05-17  ██████████████████   773 calls  $  167.04
  2026-05-18  █████████████████    742 calls  $  288.81
  2026-05-19  ██████████           340 calls  $  242.24
  2026-05-20  ████████████         419 calls  $   97.66
```

## Features

### Cost Analysis (real token data)

AgentMeter reads your agent's actual API usage from its session transcripts
— no estimates, no approximations.

```
$ agentmeter cost

  AgentMeter  —  $21.39  (7,783,047 tokens, 146 LLM calls)
  ──────────────────────────────────────────────────────────
    Cache reads:        7,272,977  (93%)
    Cache creation:       463,809  (6%)
    Output:                18,230  (0.2%)
    Input:                 28,031  (0.4%)
```

### Tool Call Stats

```
$ agentmeter stats

  Total: 847 calls | 3 errors | 142ms avg tool time

  Read                 ████████████████████   312 calls
  Edit                 ████████████           198 calls
  Bash                 █████████              147 calls
  Grep                 ██████                  89 calls
```

### Web Dashboard

```bash
agentmeter dashboard
```

Opens a local dashboard at `localhost:8070` with six views: overview KPIs,
project breakdown, session list, daily charts, strategy recommendations,
and rate card management.

### Spend Forecasting

```bash
agentmeter forecast    # projected monthly spend
agentmeter advise      # spend analysis with recommendations
agentmeter strategy    # per-project cost analysis and advice
agentmeter summary     # compact cost context (for agent injection)
```

### Budget Enforcement

Set limits and AgentMeter will block or warn when they're exceeded. Denials
return informative errors the agent can reason about — not crashes.

```bash
agentmeter budget set session 50          # max 50 calls per session
agentmeter budget set daily 200           # max 200 calls per day
agentmeter budget set daily 100 -s mail   # per-server daily limit
agentmeter budget set session 30 -a warn  # warn but don't block
```

### Circuit Breakers

Velocity-based protection against runaway loops:

```bash
agentmeter breaker set 20 60     # trip after 20 calls in 60 seconds
agentmeter breaker set 10 30 -c 600  # custom cooldown (600s)
```

### Data Export

```bash
agentmeter export                          # JSONL to stdout
agentmeter export --tool Read --since 2026-05-01 --limit 100
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

Cost data comes from reading the agent's own session transcript files, which
contain real token counts from the API response.

### Architecture

- **Local-first** — SQLite with WAL mode, works offline, no cloud dependency
- **Transparent** — never modifies tool call data, just observes
- **Cross-platform** — Linux, macOS, Windows
- **Fast hooks** — <5ms overhead, stdlib only, never crashes the agent

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Supported Agents

| Agent | Hook Type | Status |
|-------|-----------|--------|
| Claude Code | PostToolUse | Full support |
| Gemini CLI | AfterTool | Full support |
| Codex CLI | PostToolUse | Full support |
| Copilot CLI | postToolUse | Full support |

## CLI Reference

| Command | Description |
|---------|-------------|
| `agentmeter stats` | Tool call stats (today, `--week`, `--all`) |
| `agentmeter calls` | Recent individual calls |
| `agentmeter sessions` | Session breakdowns with outcomes |
| `agentmeter daily` | Daily totals with bar chart |
| `agentmeter cost` | Real token cost per session |
| `agentmeter forecast` | Monthly spend projection |
| `agentmeter advise` | Spend analysis + recommendations |
| `agentmeter strategy` | Per-project cost analysis |
| `agentmeter summary` | Compact cost context for agents |
| `agentmeter dashboard` | Web dashboard (localhost:8070) |
| `agentmeter export` | JSONL data export |
| `agentmeter budget` | Budget rules (set/show/clear) |
| `agentmeter breaker` | Circuit breakers (set/show/clear) |
| `agentmeter hook` | Hook management (install/status) |
| `agentmeter rates` | View/edit rate card |
| `agentmeter wrap` | MCP proxy mode |
| `agentmeter backfill` | Detect outcomes in historical sessions |

## Requirements

- Python 3.11+
- No external services or API keys

## Developing

```bash
git clone https://github.com/your-org/agentmeter
cd agentmeter
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

## Licence

Apache 2.0 — see [LICENSE](LICENSE).
