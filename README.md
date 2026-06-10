# AgentMeter

**Know what your agents cost.**

AgentMeter is the cost intelligence layer for AI coding agents. It captures
every tool call, tracks real token costs, analyses caching efficiency, and
coaches you on reducing spend.

Built for **Claude Code**. Also supports Gemini CLI, Codex CLI, and
Copilot CLI. Runs entirely on your machine. No cloud, no accounts, no signup.

## Why

AI coding agents are powerful, but they're expensive and opaque. A single
session can burn through $50+ in API costs, and you won't know until the
bill arrives. Prompt caching splits costs into three token types at three
different rates. Your API dashboard shows per-request numbers. AgentMeter
shows you the real picture.

```
$ agentmeter cost

  ProjectX  —  $19.17  (4,982,859 tokens, 83 LLM calls)
  ──────────────────────────────────────────────────────────────
    Cache reads:        4,358,360  (87%)
    Cache creation:       592,819  (12%)
    Output:                17,284  (0.3%)
    Input:                 14,396  (0.3%)
    Cache efficiency: 88%
    Cache saved:     $58.84 (75%)
```

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

## Features

### Cost Analysis

Real token costs from Claude Code session transcripts — not estimates:

```bash
agentmeter cost               # recent sessions with token breakdown
agentmeter cost <session-id>  # detailed breakdown (partial ID works)
agentmeter forecast           # monthly spend projection
agentmeter strategy           # per-project cost analysis + advice
agentmeter model              # model tier analysis + what-if comparison
```

Cache intelligence is built in. Every cost view shows cache efficiency
(how well prompt caching is working) and cache savings (dollars saved
vs uncached).

### Value Multiplier

Answers the question: **"Did the agent save more than it cost?"**

```
$ agentmeter value

  Value Report  (dev rate: $150/hr)
  ────────────────────────────────────────────────────────────────────────
  AgentMeter             cost   $21.43  value $5017.50  mult 234.1x  quality  95 (good)
                         time    2007m  outcomes 3c 909t 12f 3l
  LLMM                   cost   $57.06  value $10845.00  mult 190.0x  quality 100 (good)
                         time    4338m  outcomes 8c 1880t 40f 6l
  ────────────────────────────────────────────────────────────────────────
  Total                  cost $ 417.15  value $37505.00  mult 89.9x  time 15002m saved
```

Each session gets:
- **Cost** — real token spend from API rates
- **Value** — estimated developer time saved, based on commits, tests, files changed, lint passes
- **Multiplier** — value / cost. Anything >1x means you came out ahead
- **Quality score** (0-100) — penalised for errors, test failures, retries, lint errors

```bash
agentmeter value                # recent sessions
agentmeter value -p MyProject   # filter by project
agentmeter value --rate 200     # set dev hourly rate ($150 default)
```

### Session Coaching

Analyses tool call patterns and gives actionable advice:

```bash
agentmeter coach review              # review most recent session
agentmeter coach review -p myproject # review by project name
agentmeter advise                    # cross-session spend analysis
```

```
$ agentmeter coach review -p myproject

  Session Review — MyProject
  2026-05-28 14:03
  ───────────────────────────────────────────────────────
  131 tool calls  $68.98

    Bash                  █████    39 (30%)
    Edit                  █████    34 (26%)
    Read                  ███    22 (17%)

  Outcome: 2 commits, 27 files changed, 1188 tests passed

  Efficiency: 8/10

  Patterns detected:
   ! Grep+Glob called 16x (Grep 12, Glob 4)
     Tell the agent what you're looking for and where.
     Read 17 unique files
     You're exploring broadly. Invest 2 min writing which files matter.
```

Detects 13 patterns including edit-test loops, broad exploration, repeated
file reads, high velocity bursts, cache write waste, and low cache efficiency.

### Yellow Card Coaching

Real-time mid-session nudges. When your session crosses a cost threshold,
AgentMeter blocks one tool call with a coaching message — the agent sees it,
adjusts, and retries.

```bash
agentmeter coach start              # session-start profiling + guidance
agentmeter coach start -t development  # set task type
agentmeter coach context            # generate CLAUDE.md coaching block
agentmeter coach review             # post-session efficiency analysis
agentmeter coach show               # current thresholds
agentmeter coach set calls 50 100   # set call thresholds
agentmeter coach set repeat 15      # repeated tool threshold
agentmeter coach on                 # enable yellow cards
agentmeter coach off                # disable yellow cards
```

### Tool Call Stats

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

### Web Dashboard

```bash
agentmeter dashboard          # open at localhost:8070
```

Six views: overview, projects with cost split, sessions, daily trends,
rate card, and strategy recommendations.

### Agent Context Injection

Generate a cost summary your agent can read at session start:

```bash
agentmeter summary                    # all projects
agentmeter summary -p myproject       # project-specific
agentmeter summary >> CLAUDE.md       # inject into agent context
```

### MCP Proxy

If you run MCP servers, AgentMeter can sit between your agent and the server
as a transparent proxy:

```bash
agentmeter wrap python -m some.mcp.server
agentmeter wrap --name myserver python -m some.mcp.server
```

Hook data and proxy data feed the same database — built-in tools and MCP
tools in one view.

### Data Export

```bash
agentmeter export                                    # JSONL to stdout
agentmeter export --tool Read --since 2026-05-01     # filtered
```

## How It Works

Two capture paths feed the same SQLite database:

```
Path 1: Hook (primary — works with any agent that has a hook system)
  Agent's built-in tools -> PostToolUse hook -> agentmeter -> SQLite DB

Path 2: MCP Proxy (for metering MCP server traffic)
  Agent -> AgentMeter proxy -> MCP Server -> SQLite DB
```

### Architecture

- **Local-first** — SQLite with WAL mode, works offline, no cloud dependency
- **Transparent** — never modifies tool call data, just observes
- **Cross-platform** — Linux, macOS, Windows
- **Fast hooks** — <5ms overhead, stdlib only, never crashes the agent

## Supported Agents

| Agent | Hook Type | Status |
|-------|-----------|--------|
| Claude Code | PostToolUse | Full support (primary) |
| Gemini CLI | AfterTool | Adapter built, untested in production |
| Codex CLI | PostToolUse | Adapter built, untested in production |
| Copilot CLI | postToolUse | Adapter built, untested in production |

## CLI Reference

Commands are grouped — run `agentmeter --help` to see them by category.

**Getting Started**

| Command | Description |
|---------|-------------|
| `agentmeter hook install <agent>` | Install hook for claude/gemini/codex/copilot |
| `agentmeter hook status` | Check which hooks are installed |
| `agentmeter wrap <command>` | MCP proxy mode (advanced) |
| `agentmeter stats` | Tool call stats (today, `--week`, `--all`) |
| `agentmeter sessions` | Session breakdowns with outcomes |
| `agentmeter calls` | Recent individual calls |

**Cost Analysis**

| Command | Description |
|---------|-------------|
| `agentmeter cost` | Real token costs per session |
| `agentmeter daily` | Daily totals with bar chart and cost |
| `agentmeter forecast` | Monthly spend projection |
| `agentmeter value` | Value multiplier — cost vs dev time saved |
| `agentmeter model` | Model tier analysis + what-if cost comparison |
| `agentmeter rates` | View/edit model rate card |

**Intelligence**

| Command | Description |
|---------|-------------|
| `agentmeter advise` | Cross-session spend recommendations |
| `agentmeter strategy` | Per-project cost analysis + advice |
| `agentmeter summary` | Cost context for agent injection |
| `agentmeter coach review` | Post-session efficiency analysis |
| `agentmeter coach start` | Session-start profiling + guidance |
| `agentmeter coach on/off` | Enable/disable yellow card coaching |

**Governance**

| Command | Description |
|---------|-------------|
| `agentmeter budget` | Budget rules (set/show/clear) |
| `agentmeter breaker` | Circuit breakers (set/show/clear) |

**Data**

| Command | Description |
|---------|-------------|
| `agentmeter export` | JSONL data export |
| `agentmeter backfill` | Detect outcomes in historical sessions |
| `agentmeter rename` | Rename a session |
| `agentmeter dashboard` | Web dashboard (6 views) |

Most commands accept `-p/--project` to filter by project name.

## Requirements

- Python 3.11+
- No external services or API keys

## Licence

Apache 2.0 — see [LICENSE](LICENSE).
