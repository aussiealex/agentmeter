# Spec: Universal Session Reader

## Problem

AgentMeter supports four coding agents but only reads real token data from
one. Claude Code gets accurate cost via JSONL session files. Gemini, Codex,
and Copilot get rate-card estimates based on tool call byte sizes — which
are guesses.

All four agents write real API token data to local files. We're reading 1/4.

| Agent | Tool calls | Token data | Accuracy |
|-------|-----------|------------|----------|
| Claude Code | PostToolUse hook | session_reader.py (JSONL) | Real API data |
| Codex CLI | PostToolUse hook | Not reading | Rate card guess |
| Copilot CLI | postToolUse hook | Not reading | Rate card guess |
| Gemini CLI | AfterTool hook | Not reading | Rate card guess |

This is the core value gap. "Know what your agents cost" requires knowing,
not guessing.

## Solution

Extend `session_reader.py` into a multi-agent session reader that finds and
parses each agent's transcript files at **query time** (not in the hook hot
path). Plus add a Gemini AfterModel hook for real-time token capture.

## Agent Transcript Locations and Formats

### Claude Code (existing — no changes)

**Location:** `~/.claude/projects/<slug>/<session-id>.jsonl`
**Format:** JSONL, one line per message
**Token source:** `type: "assistant"` lines → `message.usage`

```json
{"type": "assistant", "message": {"model": "claude-sonnet-4-6", "usage": {
  "input_tokens": 245, "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 1024, "output_tokens": 89}}}
```

**Parsing:** Sum usage fields across all assistant messages. Already built.

### Codex CLI (new reader)

**Location:** `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
**Env override:** `CODEX_HOME` overrides `~/.codex`
**Format:** JSONL, event-typed lines
**Token source:** `payload.type == "token_count"` events

```json
{"timestamp": "2025-10-17T05:54:20.209Z", "type": "event_msg",
 "payload": {"type": "token_count", "info": {
   "total_token_usage": {
     "input_tokens": 26549, "cached_input_tokens": 22272,
     "output_tokens": 1590, "reasoning_output_tokens": 1152,
     "total_tokens": 28139},
   "last_token_usage": {...},
   "model_context_window": 272000}}}
```

**Parsing:**
- Find all `token_count` events in the rollout file
- Use the LAST `total_token_usage` — it's cumulative
- Do NOT sum `last_token_usage` values (overcounts)
- Map: `input_tokens` → input, `cached_input_tokens` → cache_read,
  `output_tokens` → output, `reasoning_output_tokens` → tracked separately

**Gotcha — subagent replay inflation:**
Codex spawns subagent threads whose rollout files replay parent token history.
Naive parsing of all rollout files inflates by up to 91x. Solution: only read
the main rollout file per session, identified by matching `session_id`.

**Gotcha — duplicate events on rate-limit updates:**
Codex can emit `token_count` events with unchanged totals when only rate
limits change. Using the last cumulative total (not summing deltas) avoids
this naturally.

**Finding the right file:**
The hook payload includes `session_id` and (per OpenAI docs) a
`transcript_path` field pointing to the rollout file. We can:
1. Store `transcript_path` when the hook fires (add to session metadata)
2. Or scan `~/.codex/sessions/` matching session dates from the DB

Option 1 is cleaner. Extend the Codex hook to capture `transcript_path`
from the payload and store it on the session row.

### Copilot CLI (new reader)

**Location:** `~/.copilot/session-state/<session-id>/events.jsonl`
**Env override:** `COPILOT_HOME` overrides `~/.copilot`
**Format:** JSONL, event-typed lines
**Token source:** `session.shutdown` event → `modelMetrics`

```json
{"type": "session.shutdown", "modelMetrics": {
  "claude-sonnet-4-6": {
    "inputTokens": 5000, "cachedInputTokens": 3000,
    "cacheWriteTokens": 500, "outputTokens": 1200,
    "reasoningTokens": 0,
    "requests": {"count": 12, "cost": 24}}}}
```

**Parsing:**
- Find the `session.shutdown` event (one per session, at end of file)
- Sum across all models in `modelMetrics` (session may use multiple)
- Map: `inputTokens` → input, `cachedInputTokens` → cache_read,
  `cacheWriteTokens` → cache_create, `outputTokens` → output
- `requests.cost` gives premium request units (Copilot's billing metric)

**Gotcha — data only at shutdown:**
Token data appears in the `session.shutdown` event, written when the session
ends. For live sessions, data won't be available until the user exits Copilot.
This is fine for `agentmeter cost` (post-session analysis) but means no
mid-session cost data.

**Finding the right file:**
Copilot uses session IDs as directory names. The path is deterministic:
`~/.copilot/session-state/{session_id}/events.jsonl`. Direct match.

### Gemini CLI (new reader + new hook)

**Two approaches — implement both:**

#### Approach 1: AfterModel hook (real-time, preferred)

Gemini CLI has an `AfterModel` hook that fires after each LLM call and
includes `usageMetadata`:

```json
{"usageMetadata": {
  "promptTokenCount": 245, "candidatesTokenCount": 89,
  "totalTokenCount": 334, "cachedContentTokenCount": 1024,
  "thoughtsTokenCount": 50}}
```

New file: `src/agentmeter/hooks/gemini_model.py`
- Entry point: `python3 -m agentmeter.hooks.gemini_model`
- Reads AfterModel payload from stdin
- Writes token data directly to a new `llm_call` table (or appends to
  session token accumulator)
- No file parsing needed — data comes in real-time

Installed alongside the AfterTool hook:
```json
{"hooks": {
  "AfterTool": [{"command": "python3 -m agentmeter.hooks.gemini"}],
  "AfterModel": [{"command": "python3 -m agentmeter.hooks.gemini_model"}]
}}
```

#### Approach 2: Session file reader (historical data)

**Location:** `~/.gemini/tmp/<project_hash>/chats/*.json`
**Env override:** `GEMINI_HOME` overrides `~/.gemini`
**Format:** JSON (full rewrite per message — may migrate to JSONL)
**Token source:** Usage metadata embedded in session JSON

**Gotcha — format instability:**
Gemini CLI is actively migrating from JSON to JSONL format (PR #23749). The
reader must handle both formats. The AfterModel hook is more stable — it
uses the hook protocol, not the file format.

**Finding the right file:**
Gemini hashes the project root path to create the directory name under
`~/.gemini/tmp/`. We need to either:
1. Replicate the hash function (fragile)
2. Scan all chat directories matching session timestamps
3. Rely on the AfterModel hook (avoids file reading entirely)

Option 3 is strongly preferred.

## Data Model Changes

### Session metadata extension

Add an optional `transcript_path` column to the session table:

```sql
ALTER TABLE session ADD COLUMN transcript_path TEXT NOT NULL DEFAULT '';
```

Populated by:
- **Codex hook:** from `transcript_path` in the payload
- **Copilot:** deterministic from session_id
  (`~/.copilot/session-state/{id}/events.jsonl`)
- **Claude Code:** deterministic from session_id + project slug
- **Gemini:** not needed if using AfterModel hook

### Token accumulator for Gemini AfterModel

The AfterModel hook fires per LLM call (not per tool call). We need
somewhere to accumulate per-session token totals. Options:

**Option A: New `llm_call` table**
```sql
CREATE TABLE IF NOT EXISTS llm_call (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    agent           TEXT NOT NULL,
    model_id        TEXT NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_create_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

This records each LLM call individually. The session reader sums them.
Works for Gemini AfterModel data and could also store Codex/Copilot data
if we later want to capture per-call granularity.

**Option B: Accumulate on session row**
Add token columns to the session table. The AfterModel hook increments them.

Option A is cleaner — it's additive, stores facts (individual calls), and
derives totals at query time. Matches the "store facts, derive economics"
principle.

## Architecture

### Reader dispatch

`session_reader.py` becomes the entry point for all agents. It dispatches
based on the `agent` column from the session:

```python
def read_session_tokens(session: Session) -> SessionTokens | None:
    """Read real token data for any agent's session."""
    agent = session.server_name  # "claude-code", "codex-cli", etc.

    if agent == "claude-code":
        return _read_claude(session)
    elif agent == "codex-cli":
        return _read_codex(session)
    elif agent == "copilot-cli":
        return _read_copilot(session)
    elif agent == "gemini-cli":
        return _read_gemini(session)
    else:
        return None
```

Each `_read_*` function knows where to find files and how to parse them.

Alternatively, split into separate reader modules:
```
src/agentmeter/readers/
    __init__.py        # dispatch function
    claude.py          # existing logic from session_reader.py
    codex.py           # new
    copilot.py         # new
    gemini.py          # new (reads from llm_call table, not files)
```

This keeps each reader focused and under the module size ceiling.

### CLI integration

`cli_cost.py` currently hardcodes Claude Code:
- `find_session_jsonl()` looks for Claude Code paths only
- Error messages say "Only Claude Code sessions have token data"

Changes needed:
1. Replace `find_session_jsonl()` call with `read_session_tokens(session)`
2. The dispatch function handles all agents
3. Remove Claude-specific error messages
4. `agentmeter cost` works for any agent with token data

Same changes needed in: `cli_forecast.py`, `cli_advise.py`,
`cli_strategy.py`, `cli_summary.py` — anywhere that calls
`find_session_jsonl` or `read_session_tokens_from_file`.

### SessionTokens model extension

Add a field for reasoning tokens (Codex and Copilot report these):

```python
@dataclass
class SessionTokens:
    session_id: str = ""
    model_id: str = ""
    agent: str = ""                  # NEW — which agent produced this
    llm_call_count: int = 0
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0        # NEW — OpenAI reasoning tokens
```

Reasoning tokens are included in output_tokens by OpenAI but reported
separately. Store both for accurate cost calculation (reasoning may
have different pricing in the future).

## Hook Changes

### Codex hook extension

Extend `hooks/codex.py` to capture `transcript_path` from the payload
and store it on the session:

```python
# In main(), after parsing:
transcript_path = data.get("transcript_path", "")
# Pass to record_event or store on session directly
```

Minimal change — one extra field captured, stored on session row.

### Gemini AfterModel hook (new)

New file: `src/agentmeter/hooks/gemini_model.py` (~50 lines)

```python
"""Gemini CLI AfterModel hook — captures real token data per LLM call."""

def main() -> None:
    data = json.loads(sys.stdin.read())
    usage = data.get("usageMetadata", {})

    session_id = data.get("session_id", "unknown")
    model = data.get("model", "")

    # Write to llm_call table
    db = MeterDB()
    db.record_llm_call(LLMCall(
        session_id=session_id,
        agent="gemini-cli",
        model_id=model,
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0),
        cache_read_tokens=usage.get("cachedContentTokenCount", 0),
        reasoning_tokens=usage.get("thoughtsTokenCount", 0),
    ))
    db.close()
```

### Hook install update

`agentmeter hook install` gains:
- Gemini: adds AfterModel hook alongside AfterTool
- All agents: no other install changes needed

## File Finder Functions

### Codex file finder

```python
def find_codex_rollout(session_id: str, transcript_path: str = "") -> Path | None:
    """Find Codex session rollout file.

    Prefers transcript_path from hook payload if stored.
    Falls back to scanning ~/.codex/sessions/ by date.
    """
    if transcript_path:
        p = Path(transcript_path)
        if p.exists():
            return p

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return None

    # Scan date directories for matching rollout
    for rollout in sorted(sessions_dir.rglob("rollout-*.jsonl"), reverse=True):
        # Quick check: does this file contain our session_id?
        # Read first few lines only
        ...
```

### Copilot file finder

```python
def find_copilot_events(session_id: str) -> Path | None:
    """Find Copilot session events file. Path is deterministic."""
    copilot_home = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
    events = copilot_home / "session-state" / session_id / "events.jsonl"
    return events if events.exists() else None
```

## Parsing Details

### Codex parser

```python
def read_codex_tokens(path: Path) -> SessionTokens | None:
    """Read token data from a Codex rollout JSONL file.

    Uses the LAST token_count event's total_token_usage (cumulative).
    Does NOT sum last_token_usage values (causes overcounting).
    """
    last_usage = None
    model_id = ""

    with open(path) as f:
        for line in f:
            data = json.loads(line.strip())
            if data.get("type") != "event_msg":
                continue
            payload = data.get("payload", {})
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info", {})
            last_usage = info.get("total_token_usage", {})

            # Extract model from other events if available
            ...

    if not last_usage:
        return None

    return SessionTokens(
        input_tokens=last_usage.get("input_tokens", 0),
        cache_read_tokens=last_usage.get("cached_input_tokens", 0),
        output_tokens=last_usage.get("output_tokens", 0),
        reasoning_tokens=last_usage.get("reasoning_output_tokens", 0),
        # llm_call_count not directly available from cumulative totals
    )
```

### Copilot parser

```python
def read_copilot_tokens(path: Path) -> SessionTokens | None:
    """Read token data from a Copilot events.jsonl file.

    Looks for the session.shutdown event with modelMetrics.
    Sums across all models (session may use multiple).
    """
    tokens = SessionTokens()

    with open(path) as f:
        for line in f:
            data = json.loads(line.strip())
            if data.get("type") != "session.shutdown":
                continue

            metrics = data.get("modelMetrics", {})
            for model_id, model_data in metrics.items():
                tokens.input_tokens += model_data.get("inputTokens", 0)
                tokens.cache_read_tokens += model_data.get("cachedInputTokens", 0)
                tokens.cache_creation_tokens += model_data.get("cacheWriteTokens", 0)
                tokens.output_tokens += model_data.get("outputTokens", 0)
                tokens.reasoning_tokens += model_data.get("reasoningTokens", 0)
                tokens.llm_call_count += model_data.get("requests", {}).get("count", 0)

                if not tokens.model_id:
                    tokens.model_id = model_id

            break  # Only one shutdown event

    return tokens if tokens.llm_call_count > 0 else None
```

### Gemini reader (from llm_call table)

```python
def read_gemini_tokens(session_id: str, db: MeterDB) -> SessionTokens | None:
    """Read token data from llm_call table (written by AfterModel hook)."""
    rows = db.get_llm_calls(session_id=session_id)
    if not rows:
        return None

    tokens = SessionTokens(session_id=session_id, agent="gemini-cli")
    for row in rows:
        tokens.input_tokens += row.input_tokens
        tokens.output_tokens += row.output_tokens
        tokens.cache_read_tokens += row.cache_read_tokens
        tokens.reasoning_tokens += row.reasoning_tokens
        tokens.llm_call_count += 1
        if not tokens.model_id:
            tokens.model_id = row.model_id

    return tokens
```

## Performance

- **File reading happens at query time** (CLI commands), not in hooks
- Codex/Copilot files are read once per `agentmeter cost` invocation
- Gemini data is already in the DB (AfterModel hook writes it)
- No change to hook latency (<5ms constraint preserved)
- File reads are bounded: Codex reads last event only (seek to end),
  Copilot reads one shutdown event, Claude sums all assistant messages

## Testing

- Unit tests per reader: known JSONL fixtures → expected SessionTokens
- Test Codex subagent inflation protection (fixture with replayed data)
- Test Copilot multi-model sessions (fixture with 2+ models in shutdown)
- Test Gemini AfterModel hook writes to llm_call table
- Test dispatch: agent column → correct reader called
- Test missing files: reader returns None gracefully
- Integration: `agentmeter cost` shows real data for each agent type

## Implementation Order

1. `llm_call` table + schema migration
2. `readers/` package with dispatch + Claude reader (extract from session_reader.py)
3. Codex reader + file finder
4. Copilot reader + file finder
5. Gemini AfterModel hook (`hooks/gemini_model.py`)
6. Gemini reader (from llm_call table)
7. Update `cli_cost.py` to use dispatch (remove Claude-only logic)
8. Update `cli_forecast.py`, `cli_advise.py`, `cli_strategy.py`, `cli_summary.py`
9. Extend Codex hook to capture `transcript_path`
10. Update `agentmeter hook install` for Gemini AfterModel
11. `SessionTokens.reasoning_tokens` field + display in CLI
12. `session.transcript_path` column migration
13. Tests for all readers + integration

## Migration Path

- `session_reader.py` stays as-is initially (backwards compat)
- New `readers/` package adds dispatch layer
- CLI modules switch from `session_reader` imports to `readers` imports
- Once all CLI modules are migrated, `session_reader.py` becomes a thin
  re-export wrapper (or is removed if nothing external imports it)
- `python3 -m agentmeter.hook` still works (no change to hook entry points)
