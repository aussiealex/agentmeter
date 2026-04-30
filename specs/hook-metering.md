# Spec: Hook-Based Metering for Built-In Tools

## Problem

AgentMeter only meters MCP tool calls (via proxy). Claude Code's built-in
tools (Read, Edit, Bash, Grep, Glob, Write, Agent, WebFetch, etc.) are
invisible — they never pass through the proxy. This means most of the
agent's actual work is unmetered.

## Solution

Use Claude Code's PostToolUse hook to log every built-in tool call to the
same SQLite DB. All existing CLI commands (`stats`, `sessions`, `daily`,
`stats --distribution`) work unchanged — they just see more data.

## Hook Input Format

Claude Code sends JSON on stdin to PostToolUse hooks:

```json
{
  "session_id": "abc123",
  "tool_name": "Edit",
  "tool_input": {
    "file_path": "/path/to/file",
    "old_string": "...",
    "new_string": "..."
  },
  "tool_response": "The file has been updated successfully."
}
```

Fields available: `session_id`, `tool_name`, `tool_input` (dict),
`tool_response` (string or dict).

## Design

### Hook script: `src/agentmeter/hook.py`

- Reads JSON from stdin
- Skips `mcp__*` tool names (already metered by the proxy — no double-counting)
- On first call for a `session_id`, creates a session row with
  `server_name = "claude-code"`
- Writes a `tool_call` row with:
  - `session_id` — from hook input
  - `server_name` — `"claude-code"`
  - `tool_name` — from hook input
  - `arguments_json` — `json.dumps(tool_input)`, truncated to 1000 chars
  - `result_json` — `str(tool_response)`, truncated to 2000 chars
  - `result_size` — `len(str(tool_response))` bytes
  - `is_error` — `0` (PostToolUse only fires on success)
  - `elapsed_ms` — `0` (not available from PostToolUse alone)
  - `started_at` — `datetime.now().isoformat()`

### Configuration

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 -m agentmeter.hook",
            "timeout": 2000
          }
        ]
      }
    ]
  }
}
```

### Session lifecycle

- No explicit "session end" signal from hooks
- Leave `ended_at` null; backfill lazily (e.g., when querying, treat
  sessions with no calls in the last 30 minutes as ended)
- Alternatively, add a `PreCompact` or `Stop` hook if Claude Code
  exposes one

### Double-counting prevention

MCP tool calls flow through both the proxy AND the hook. Filter in the
hook script:

```python
if tool_name.startswith("mcp__"):
    sys.exit(0)
```

### Server name convention

| Source | server_name |
|--------|------------|
| MCP proxy (existing) | `"mailsift"`, `"testserver"`, etc. |
| Hook (new) | `"claude-code"` |

This lets `agentmeter stats --server claude-code` show only built-in tool
usage, and `--distribution` naturally groups them separately.

## Performance

- SQLite write: ~1ms (WAL mode, no contention)
- JSON parse from stdin: <1ms
- Total hook overhead: <5ms per tool call
- Hook timeout set to 2000ms as safety margin
- No network calls, no imports beyond stdlib + agentmeter.db

## What This Unlocks

- Full visibility into agent behavior across all tools
- Distribution analytics comparing built-in vs MCP usage patterns
- Budget enforcement on built-in tools (e.g., limit Bash calls per session)
- Session-level cost modeling (combine built-in volume with MCP latency)
- Data for "value multiplier" reporting (total agent activity vs outcomes)

## Future: Elapsed Time

PostToolUse alone can't measure elapsed_ms. Two options:

1. **PreToolUse + PostToolUse pair** — PreToolUse writes start time to a
   temp file keyed by session_id + invocation counter, PostToolUse reads
   it and computes delta. Adds complexity and a second hook.
2. **Skip it** — volume and result_size are more useful than latency for
   built-in tools (Read/Edit are always fast, Bash varies by command).
   Latency matters more for MCP calls which already have it.

Recommend option 2 for v1.

## Files to Create/Modify

- `src/agentmeter/hook.py` — new, ~50 lines
- `src/agentmeter/__main__.py` or `cli.py` — optional `agentmeter hook install`
  command that writes the settings.json config for the user
- Tests: `tests/test_hook.py` — feed sample JSON via stdin, verify DB rows

## Open Questions

- Should the hook capture the working directory to infer which project
  the session belongs to? Would allow per-project analytics without
  needing MCP server names.
- Should `agentmeter hook install` be interactive (asks confirmation) or
  just print the JSON for the user to paste?
- Should errored tool calls be captured? PostToolUse only fires on
  success. A PreToolUse + error handling path would be needed for errors.
