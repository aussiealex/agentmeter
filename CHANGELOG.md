# Changelog

All notable changes to AgentMeter are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions
use [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-05-30

### Added
- **Cache intelligence** — cache efficiency metric (token hit rate) and
  cache savings (dollars saved vs uncached) in `cost`, `strategy`, and
  dashboard views.
- **Cache waste heuristics** — `cache_write_waste` detects short sessions
  where the 1.25x write premium wasn't recouped. `low_cache_efficiency`
  detects high-volume sessions with poor cache hit rate.
- **Session coaching** — `agentmeter coach review` analyses a single session
  for efficiency patterns with a 1-10 score. Supports `-p` project filter.
- **Heuristics engine** — 13 pattern detectors for tool call analysis.
  Consumed by `advise`, `coach review`, and the web dashboard.
- **Web dashboard** — `agentmeter dashboard` serves a local dashboard at
  localhost:8070 with six views: overview, projects, sessions, daily,
  strategy, and rate card.
- **Cross-platform support** — Linux, macOS, Windows data directories.
- **Real token cost analysis** — `agentmeter cost` reads actual API token
  counts from Claude Code session transcripts.
- **Spend forecasting** — `agentmeter forecast` projects monthly spend.
- **Spend advisory** — `agentmeter advise` with project filtering (`-p`).
- **Strategy command** — `agentmeter strategy` per-project cost analysis
  with cache efficiency, savings, and recommendations.
- **Summary command** — `agentmeter summary` for agent context injection.
- **Session outcomes** — commits, files changed, and test results tracking.
- **Data export** — `agentmeter export` outputs JSONL.
- **Cache write rate** — separate cache write pricing on rate card.

### Changed
- All features now ship in the package — no pro/free tier split.
- Agent-agnostic advice — no platform-specific recommendations.
- Licence field corrected to Apache-2.0.

## [0.4.0] — Multi-Agent Foundation

### Added
- **Multi-agent hook system** — adapters for Claude Code, Gemini CLI, Codex CLI,
  and GitHub Copilot CLI. Each is a thin adapter (~80 lines) that normalises
  agent-specific payloads into a shared `NormalisedToolEvent`.
- **`agentmeter hook install <agent>`** — generates correct hook config for
  Claude (JSON), Gemini (JSON), Codex (TOML), or Copilot (JSON).
- **`agentmeter hook status [--agent <name>]`** — per-agent or all-agent stats.
- **Rate card table** — seeded with 9 models (Anthropic, Google, OpenAI).
  Configurable per-model pricing for query-time cost estimation.
- **Schema additions** — `agent`, `project`, `model_id`, `input_size` columns
  on `tool_call` table. Additive migration, backwards compatible.
- **`NormalisedToolEvent` dataclass** — common event format for all hook adapters.
- **`RateCard` dataclass** — model pricing for cost estimation.
- **`DailyTotal` and `BreakerTrip` dataclasses** — replaced dict returns.
- **`PROJECT_BRIEF.md`** — project overview and current status.
- **`ARCHITECTURE.md`** — system design, layer contracts, data flow.
- **`CHANGELOG.md`** — this file.
- **`LICENSE`** — Apache 2.0.

### Changed
- **Split `db.py` (814 lines) into `db/` package** — 8 focused modules
  (schema, sessions, calls, budget, breaker, rates, analytics, helpers).
  All under 200 lines each.
- **Split `cli.py` (518 lines) into CLI submodules** — cli_budget, cli_breaker,
  cli_hook, cli_format. Core commands stay in cli.py (211 lines).
- **Refactored `hook.py`** into `hooks/` package. `hook.py` is now a
  backwards-compatible shim that imports from `hooks/claude.py`.

## [0.3.0] — 2026-05-07

### Added
- **PostToolUse hook** for metering Claude Code's built-in tools (Read, Edit,
  Bash, Grep, etc.) into the same SQLite DB used by the MCP proxy.
- **Session handoff protocol** spec for multi-session continuity.
- **Hook-metering spec** (`specs/hook-metering.md`).

## [0.2.0] — 2026-04-28

### Added
- **Session distribution analytics** — `agentmeter stats --distribution` showing
  p50/p90/p99 per-server breakdowns for calls, timing, and result size.
- **Circuit breakers** — velocity-based call gating. Trips when call rate exceeds
  threshold, blocks for configurable cooldown period.
- **Budget enforcement** — session and daily call limits with deny or warn mode.
  Budget-aware denials return informative errors the agent can reason about.

### Fixed
- Eliminated f-string SQL interpolation in 3 query methods (SQL injection fix).

## [0.1.0] — 2026-03-11

### Added
- **MCP proxy core** — transparent stdio proxy wrapping any MCP server.
  Spawns child server as subprocess, proxies all MCP traffic, records metrics.
- **SQLite metering** — tool name, timing, response size, error status.
  WAL mode for concurrent read/write.
- **CLI commands** — `wrap`, `stats`, `sessions`, `calls`, `daily`, `rename`.
- **Auto-generated session names** — `server-timeofday-toptools-Ncalls`.
- **Pytest suite** — DB unit tests, integration tests, security tests,
  boundary tests, CLI tests. GitHub Actions CI.
- **Robust error handling** in proxy (finally block, safe DB writes).
