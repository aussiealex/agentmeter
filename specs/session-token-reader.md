# Spec: Session Token Reader

## Problem

AgentMeter captures tool call volume and response sizes, but not actual token
consumption. Token data is the real cost signal. Without it, cost estimates
rely on byte-to-token heuristics that are directionally useful but not accurate.

## Discovery

Claude Code writes every message to a session JSONL file on disk. Each
assistant message includes a `usage` block with real token counts from the
Anthropic API response. This data is already local — we just need to read it.

## Session JSONL Location

```
~/.claude/projects/<project-slug>/<session-id>.jsonl
```

- `project-slug` is derived from the working directory path
  (e.g. `/media/aa/LargeBackup/MainApps/AgentMeter` becomes
  `-media-aa-LargeBackup-MainApps-AgentMeter`)
- `session-id` is a UUID matching the `session_id` from PostToolUse hooks

## JSONL Message Format

Each line is a JSON object with a `type` field. The types observed:

| Type | Count (typical session) | Contains usage? |
|------|------------------------|-----------------|
| `progress` | ~771 | No |
| `assistant` | ~293 | Yes — in `message.usage` |
| `user` | ~233 | No |
| `file-history-snapshot` | ~66 | No |
| `system` | ~46 | No |

### Assistant message structure (relevant fields only)

```json
{
  "type": "assistant",
  "sessionId": "9a91d336-...",
  "timestamp": 1779071325836,
  "message": {
    "model": "claude-opus-4-6",
    "usage": {
      "input_tokens": 16283,
      "cache_creation_input_tokens": 345735,
      "cache_read_input_tokens": 35119885,
      "output_tokens": 73974,
      "service_tier": "standard"
    }
  }
}
```

### Token types and their cost significance

| Field | What it is | Rate |
|-------|-----------|------|
| `input_tokens` | Fresh uncached input | Full input rate |
| `cache_creation_input_tokens` | First write to cache | Full input rate |
| `cache_read_input_tokens` | Served from cache | ~10% of input rate |
| `output_tokens` | Model-generated output | Output rate |

Cache reads typically dominate (80-90% of total tokens) because the
conversation history, system prompt, and CLAUDE.md are re-sent every turn.

## Design

### New module: `src/agentmeter/session_reader.py`

Pure function that reads a session JSONL and returns token totals:

```python
@dataclass
class SessionTokens:
    session_id: str = ""
    model_id: str = ""
    llm_call_count: int = 0
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

def read_session_tokens(session_id: str, project_dir: str = "") -> SessionTokens | None:
    """Read token data from Claude Code's session JSONL file."""
```

### Finding the JSONL file

1. Derive project slug from `project_dir` (or cwd)
2. Construct path: `~/.claude/projects/<slug>/<session-id>.jsonl`
3. If file doesn't exist, return None (not an error — user might not be
   using Claude Code, or session might be from another agent)

### Project slug derivation

Claude Code converts the absolute path to a slug by replacing `/` with `-`
and stripping the leading `-`:

```
/media/aa/LargeBackup/MainApps/AgentMeter
→ -media-aa-LargeBackup-MainApps-AgentMeter
```

### Cost calculation

Uses real tokens when available, with rate card lookup:

```python
def calculate_session_cost(tokens: SessionTokens, rate: RateCard) -> SessionCost:
    input_cost = tokens.input_tokens * rate.input_per_mtok / 1_000_000
    cache_create_cost = tokens.cache_creation_tokens * rate.input_per_mtok / 1_000_000
    cache_read_cost = tokens.cache_read_tokens * rate.cached_per_mtok / 1_000_000
    output_cost = tokens.output_tokens * rate.output_per_mtok / 1_000_000
    return SessionCost(
        input_cost=input_cost,
        cache_create_cost=cache_create_cost,
        cache_read_cost=cache_read_cost,
        output_cost=output_cost,
        total_cost=input_cost + cache_create_cost + cache_read_cost + output_cost,
    )
```

## Integration Points

### CLI: `agentmeter cost [session-id]`

Shows real token usage and cost for a session (or current session):

```
  Session: 9a91d336 (AgentMeter)
  Model: claude-opus-4-6
  LLM calls: 297

  Token breakdown:
    Input (uncached):         16,283    $0.24
    Cache creation:          345,735    $5.19
    Cache reads:          35,119,885   $52.68
    Output:                   73,974    $5.55
    Total:                35,555,877   $63.66

  Tool calls: 180 (via AgentMeter hook)
  Top tools: Read (52), Edit (34), Bash (28)
```

### CLI: `agentmeter daily` enhancement

When JSONL data is available, show real cost alongside call volume:

```
  2026-05-18  ████████████████  342 calls  $63.66
  2026-05-17  ████████████      218 calls  $41.20
```

### Joining hook data with token data

Both share `session_id`. The join is straightforward:

- AgentMeter hook data → per-tool-call granularity (which tools, what sizes)
- Session JSONL data → per-LLM-call granularity (real token costs)
- Together → "This session cost $63.66. Read calls were 40% of tool volume
  and drove most of the cache reads."

We don't need to attribute token cost to individual tool calls (that's not
possible — one LLM call may invoke multiple tools). The attribution is at
session level.

## Agent-Specific Considerations

### Claude Code (this spec)

Session JSONL is the primary source. Well-structured, includes full
Anthropic API usage data.

### Gemini CLI

Gemini CLI may store similar data. The `AfterModel` hook includes
`llm_response` which likely contains token usage. Investigation needed
for exact file paths and format. The session reader should be designed
to support a Gemini adapter alongside the Claude one.

### Codex CLI / Copilot CLI

Unknown whether session transcripts are stored on disk. Lower priority —
start with Claude Code where we have confirmed data.

## Edge Cases

- **File doesn't exist** — return None. The agent might be Gemini/Codex,
  or the session JSONL might have been cleaned up.
- **Partial file** — read what's there. Sessions in progress have incomplete
  data but it's still useful.
- **Format changes** — Anthropic could change the JSONL format. Wrap parsing
  in try/except, skip malformed lines, log to stderr. Never crash.
- **Multiple projects same session** — unlikely but possible if user changes
  directory mid-session. Use the project from the hook's cwd.
- **Old sessions** — JSONL files persist. Historical cost analysis works
  retroactively on all stored sessions.

## Files to Create/Modify

- `src/agentmeter/models.py` — add `SessionTokens` and `SessionCost` dataclasses
- `src/agentmeter/session_reader.py` — JSONL parser + cost calculation (~120 lines)
- `src/agentmeter/cli.py` — add `agentmeter cost` command
- `tests/test_session_reader.py` — unit tests with sample JSONL data

## Open Questions

- Should the reader run automatically on session end (via a Stop hook), or
  only on demand via CLI?
- Should token data be cached in the AgentMeter DB, or re-read from JSONL
  each time? Caching avoids re-parsing but adds staleness risk for active
  sessions.
- Should `agentmeter stats` automatically include cost data when JSONL files
  are available, or keep it as a separate `agentmeter cost` command?
- Can we detect the project slug reliably across platforms, or should the
  user configure it?
