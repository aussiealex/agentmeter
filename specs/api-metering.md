# Spec: API Call Metering (Path 3)

## Problem

AgentMeter's two capture paths both target coding agents:
- **Path 1 (hooks):** Claude Code / Gemini / Codex / Copilot tool calls
- **Path 2 (MCP proxy):** MCP server traffic

Neither captures direct API usage — when developers build apps that call
Claude, GPT, Gemini APIs directly. This is where the real money burns:

- No subscription ceiling. Every token is billed.
- A single runaway loop can cost hundreds of dollars in minutes.
- Production apps have sustained spend, not just dev-time sessions.
- Teams running agents via the API (not Claude Code) have zero local visibility.

**This is the highest-motivated user.** They're already paying per-token and
have no local-first, no-signup tool to track it.

## What Exists Today

Nothing in this space is local-first:
- **Helicone** — cloud proxy, requires account, sends data to their servers
- **Portkey** — cloud gateway, same model
- **LangSmith/Langfuse** — tracing platforms, heavy, cloud-dependent
- **Provider dashboards** — Anthropic/OpenAI usage pages. Delayed, no
  per-session or per-feature breakdown, no alerts

AgentMeter's position: **same local SQLite, same CLI, zero signup, zero cloud.**
Your API cost data never leaves your machine.

## Solution: Two Capture Methods

### Method A: SDK Wrapper (Python-first)

A thin wrapper around the official SDK client that intercepts API responses
and logs the usage block to AgentMeter's DB.

```python
import anthropic
from agentmeter import meter

# One-line change — wrap the client
client = meter(anthropic.Anthropic())

# Use normally — all calls are metered transparently
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
# AgentMeter has already logged: model, tokens in/out/cached, cost, latency
```

How it works:
1. `meter()` returns a thin proxy object that delegates all attribute access
   to the real client
2. It wraps `messages.create()` and `messages.stream()` (the two hot paths)
3. After each successful response, it extracts the `usage` block and writes
   to the DB
4. On error, it logs the error and re-raises — never swallows exceptions
5. Adds <1ms overhead (single SQLite insert, WAL mode)

What gets captured from each API response:
```python
response.usage = {
    "input_tokens": 245,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 1024,
    "output_tokens": 89,
}
response.model  # "claude-sonnet-4-6"
response.id     # "msg_01XFDUDYJgAACzvnptvVoYEL"
```

Also works with OpenAI and Google SDKs (same pattern, different usage fields):
```python
import openai
from agentmeter import meter

client = meter(openai.OpenAI())
# Same — all completions metered
```

### Method B: Local Proxy (language-agnostic)

A lightweight HTTP proxy that sits between any app and the API provider.
Zero code changes — just set an environment variable.

```bash
# Start the proxy
agentmeter api-proxy --port 8090

# Point your app at it
export ANTHROPIC_BASE_URL=http://localhost:8090/anthropic
export OPENAI_BASE_URL=http://localhost:8090/openai

# Run your app normally — all API calls are metered
python my_agent.py
```

How it works:
1. Receives API requests, forwards to the real provider endpoint
2. Reads the response, extracts usage from the response body
3. Writes to AgentMeter DB (same table, same schema)
4. Returns the response to the caller unmodified
5. Supports streaming (SSE) — buffers the final usage event

Provider routing:
- `/anthropic/*` → `https://api.anthropic.com/*`
- `/openai/*` → `https://api.openai.com/*`
- `/google/*` → `https://generativelanguage.googleapis.com/*`

The proxy never reads or stores API keys — it forwards headers as-is.

## Data Model

### New table: `api_call`

Separate from `tool_call` because the shape is different — API calls have
token counts, model IDs, and cost as first-class fields, not bolted on.

```sql
CREATE TABLE IF NOT EXISTS api_call (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL DEFAULT '',    -- app-defined or auto-generated
    request_id      TEXT NOT NULL DEFAULT '',    -- provider's response ID (msg_xxx)
    provider        TEXT NOT NULL,               -- "anthropic", "openai", "google"
    model_id        TEXT NOT NULL,
    endpoint        TEXT NOT NULL DEFAULT '',    -- "messages", "chat.completions"
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_create_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    is_error        INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    app_name        TEXT NOT NULL DEFAULT '',    -- user-defined label
    project         TEXT NOT NULL DEFAULT '',    -- from cwd or explicit
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_call_created_at ON api_call(created_at);
CREATE INDEX IF NOT EXISTS idx_api_call_model_id ON api_call(model_id);
CREATE INDEX IF NOT EXISTS idx_api_call_session_id ON api_call(session_id);
CREATE INDEX IF NOT EXISTS idx_api_call_app_name ON api_call(app_name);
```

Why a separate table (not reusing `tool_call`):
- `tool_call` stores tool name, arguments, result JSON — none of which apply
- `api_call` stores token counts and provider as first-class columns
- Queries are different — API analysis is about tokens/cost, not tool distribution
- Keeps both tables clean instead of one table with half-null columns
- Same DB file, same rate_card table for cost calculation

### Session concept for API calls

API calls don't have natural sessions like coding agents. Options:

1. **App-defined:** `meter(client, session="my-feature-v2")` — user labels calls
2. **Auto-grouped:** calls within a gap threshold (like hook sessions)
3. **App name:** `meter(client, app="my-chatbot")` — coarser grouping

Support all three. `session_id` is optional (defaults to auto-grouped).
`app_name` is the primary grouping — "which of my apps is spending?"

## SDK Wrapper Detail

### Python implementation

```python
# src/agentmeter/sdk.py

def meter(client, *, app: str = "", session: str = ""):
    """Wrap an AI SDK client to meter all API calls.

    Supports: anthropic.Anthropic, openai.OpenAI, google.generativeai
    Returns a transparent proxy — all existing code works unchanged.
    """
```

The wrapper detects the SDK type from the client class and applies the
appropriate interceptor:

**Anthropic:**
- Wraps `client.messages.create()` and `client.messages.stream()`
- Extracts `response.usage` (input_tokens, cache_creation_input_tokens,
  cache_read_input_tokens, output_tokens)
- Extracts `response.model` and `response.id`

**OpenAI:**
- Wraps `client.chat.completions.create()`
- Extracts `response.usage` (prompt_tokens, completion_tokens,
  prompt_tokens_details.cached_tokens)
- Extracts `response.model` and `response.id`

**Google:**
- Wraps `model.generate_content()`
- Extracts `response.usage_metadata` (prompt_token_count,
  candidates_token_count, cached_content_token_count)

### Streaming support

For streaming responses, the usage block arrives in the final event.
The wrapper must buffer the stream and extract usage from the terminal
message_stop / stream_end event without breaking the streaming interface.

```python
# Anthropic streaming — usage is in the final message_delta event
with client.messages.stream(...) as stream:
    for text in stream.text_stream:
        print(text)
# After stream closes, wrapper extracts stream.get_final_message().usage
```

### Async support

Both `meter(anthropic.Anthropic())` and `meter(anthropic.AsyncAnthropic())`
must work. The wrapper detects sync/async and applies the appropriate
interceptor (sync writes to DB directly, async uses anyio).

### Error handling

- If the API call fails, log the error (is_error=1) and re-raise
- If DB write fails, log to stderr and continue — never break the user's app
- If the client type is unrecognised, return it unwrapped with a stderr warning

## CLI Interface

All existing CLI commands work for API data with a `--api` flag or
naturally when API data exists:

```bash
# API-specific views
agentmeter api stats                    # API call summary (today)
agentmeter api stats --all              # all time
agentmeter api stats --app my-chatbot   # filter by app
agentmeter api daily                    # daily API spend with bar chart
agentmeter api calls                    # recent individual API calls
agentmeter api calls --model claude-sonnet-4-6

# Cost breakdown (real tokens, not estimates)
agentmeter api cost                     # cost by app
agentmeter api cost --app my-chatbot    # detailed breakdown
agentmeter api cost --model claude-sonnet-4-6

# Forecasting and alerts
agentmeter api forecast                 # projected monthly API spend
agentmeter api advise                   # optimisation recommendations

# Budget enforcement
agentmeter api budget set daily 1000 --app my-chatbot   # daily token budget
agentmeter api budget set hourly 50000                   # hourly token limit

# Export
agentmeter api export                   # JSONL export of API calls
```

### Unified view

`agentmeter cost` (no subcommand) shows combined spend across all three paths:
```
Total spend (today):
  Claude Code sessions:  $12.40  (4 sessions, 312 tool calls)
  API calls:             $34.80  (my-chatbot: $28.20, my-agent: $6.60)
  MCP proxy:              $1.20  (2 servers)
  ─────────────────────────────
  Total:                 $48.40
```

## API Proxy Detail

### Architecture

```
App → localhost:8090/anthropic/v1/messages → api.anthropic.com/v1/messages
                     ↓ (on response)
              Extract usage from response body
              Write to api_call table
              Return response unmodified
```

Built with Python's `http.server` or `aiohttp` — no heavy framework.
Single file, <300 lines.

### What the proxy does NOT do:
- Store or log API keys (forwarded as-is in headers)
- Modify request or response bodies
- Add latency beyond the DB write (<1ms)
- Require configuration beyond port number
- Phone home or send any data externally

### Streaming handling

For SSE streams (`stream: true`), the proxy:
1. Forwards each SSE event to the client immediately (no buffering delay)
2. Reads each event as it passes through
3. Extracts usage from the final `message_delta` / `[DONE]` event
4. Writes the accumulated usage to DB after stream completes

### TLS

The proxy connects to providers over HTTPS. Locally it serves HTTP
(localhost only). For non-localhost use, document how to add TLS via
a reverse proxy (nginx, caddy).

## Coaching Integration

API metering feeds directly into the coaching system:

- **Yellow card for API apps:** If an app exceeds spend thresholds, the
  CLI can alert (but can't inject mid-call like the PreToolUse hook).
  Instead: `agentmeter api watch --app my-chatbot` — terminal watcher
  that prints warnings in real-time as calls are logged.

- **Post-session review works:** `agentmeter coach review` can analyse
  API call patterns: "your chatbot made 400 calls today, 60% were cache
  misses — add system prompt caching to save $20/day."

- **Forecast is more valuable:** API users care deeply about monthly
  projections. `agentmeter api forecast` becomes a critical command.

## Provider-Specific Usage Fields

### Anthropic (Messages API)
```json
{
    "usage": {
        "input_tokens": 245,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1024,
        "output_tokens": 89
    }
}
```

### OpenAI (Chat Completions API)
```json
{
    "usage": {
        "prompt_tokens": 245,
        "completion_tokens": 89,
        "total_tokens": 334,
        "prompt_tokens_details": {
            "cached_tokens": 1024
        },
        "completion_tokens_details": {
            "reasoning_tokens": 0
        }
    }
}
```

### Google (GenerateContent API)
```json
{
    "usageMetadata": {
        "promptTokenCount": 245,
        "candidatesTokenCount": 89,
        "totalTokenCount": 334,
        "cachedContentTokenCount": 1024
    }
}
```

All three normalise into the same `api_call` columns:
- `input_tokens` — prompt/input tokens (excluding cache)
- `output_tokens` — completion/candidates tokens
- `cache_create_tokens` — Anthropic only (0 for others)
- `cache_read_tokens` — cached tokens across all providers

## Scope Boundaries

What this is:
- Local-first API cost tracking for Python apps (SDK wrapper)
- Language-agnostic API cost tracking (local proxy)
- Real token data from every API call — not estimates
- Same DB, same CLI, same rate card as agent metering
- Budget enforcement and forecasting for API spend

What this is NOT:
- A tracing/observability platform (no spans, no traces, no DAGs)
- A prompt management tool
- A caching layer (we measure, we don't optimise)
- A multi-tenant SaaS (local-first, single user)
- A replacement for provider dashboards (complementary — real-time + local)

## Success Metrics

- SDK wrapper adds <1ms to API calls
- Proxy adds <5ms latency (network round-trip dominates anyway)
- Cost accuracy within 0.1% of provider invoice
- Works with streaming without breaking the stream interface
- Zero data loss — every API call logged, even on crash (WAL mode)

## Implementation Order

1. `api_call` table + schema migration
2. SDK wrapper for Anthropic (`agentmeter.sdk`) — sync + async
3. `agentmeter api stats` and `agentmeter api cost` CLI
4. SDK wrapper for OpenAI
5. `agentmeter api daily` / `agentmeter api forecast`
6. Local HTTP proxy (`agentmeter api-proxy`)
7. SDK wrapper for Google
8. `agentmeter api budget` enforcement
9. `agentmeter api watch` — real-time terminal monitor
10. Unified cost view across all three paths

## Why This Matters

Claude Code Max subscribers pay $200/month regardless of usage. They're
cost-aware but not cost-pressured.

API users pay per token with no ceiling. A bug in a loop, a missing cache
header, a verbose system prompt — these cost real money immediately. And
they have no local tool to see it happening.

AgentMeter for API users is like a power meter for your house — you could
just wait for the bill, but seeing real-time consumption changes behaviour.
Combined with coaching, it doesn't just show the cost — it tells you how
to reduce it.
