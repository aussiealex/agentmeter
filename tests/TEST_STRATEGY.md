# AgentMeter Test Strategy

## Principles

1. **Every query method gets positive, negative, and boundary tests**
2. **Security tests use adversarial inputs at every system boundary**
3. **Error conditions test graceful degradation, not just happy paths**
4. **Integration tests verify end-to-end behavior through the proxy**
5. **CLI tests verify user-facing behavior via CliRunner**

## Implementation Status

| Category | Status | Tests |
|----------|--------|-------|
| DB unit (positive) | DONE | 23 |
| SQL injection (security) | DONE | 63 |
| Boundary & edge cases | DONE | 32 |
| CLI | DONE | 22 |
| Integration (positive) | DONE | 6 |
| Concurrency | NOT YET NEEDED | — |
| Proxy error paths | NOT YET NEEDED | — |
| XSS / output encoding | NOT YET NEEDED | — |
| Data redaction | NOT YET NEEDED | — |
| Subprocess security | NOT YET NEEDED | — |
| bandit (static analysis) | NOT YET NEEDED | — |

## When Tests Become Necessary

Tests are implemented when the use case demands them, not speculatively.
Each section below describes the trigger — the point at which the tests
move from "not yet needed" to "required before shipping."

### NOW — single user, single server (MailSift), local CLI

Already implemented:
- **DB unit tests** — core correctness
- **SQL injection** — inputs come from CLI args, which come from the user's
  shell. Low risk today but trivial to test and the cost of missing it is
  high. Done.
- **Boundary tests** — MCP tool names and results come from external servers
  and are not sanitised. Weird strings, unicode, huge payloads can happen
  in normal operation.
- **CLI tests** — the CLI is the only user interface right now.
- **Integration tests** — proxy is the product, must work end-to-end.

### When: wrapping multiple MCP servers simultaneously

**Trigger:** User configures `agentmeter wrap` for 2+ servers in
`~/.claude/settings.json`, or runs multiple agents against the same DB.

Tests needed:
- **Concurrency** — two MeterDB instances writing to the same SQLite file.
  WAL mode should handle this but it's untested. Race conditions in
  session ID generation, interleaved writes, read-during-write.
- **DB locked by another process** — what happens when SQLite returns
  SQLITE_BUSY? Retry? Crash? Silent data loss?

### When: web dashboard ships (Week 2)

**Trigger:** `agentmeter dashboard` serves HTML on localhost.

Tests needed:
- **XSS / output encoding** — tool names, server names, arguments_json,
  and result_json are all attacker-influenced (they come from MCP servers)
  and will be rendered in HTML. Must be escaped.
- **Data exposure via dashboard** — is the dashboard bound to localhost
  only? Can it be accessed from other machines on the network?

### When: wrapping untrusted or third-party MCP servers

**Trigger:** AgentMeter is used with MCP servers the user didn't write,
or servers that handle sensitive data (credentials, API keys, PII).

Tests needed:
- **Data redaction** — arguments_json and result_json store raw tool call
  data in plaintext SQLite. If a tool handles secrets, they're persisted
  forever. Need opt-in redaction or scrubbing for sensitive fields.
- **Subprocess security** — does the proxy leak environment variables
  (e.g. API keys, tokens) to the child process? Shell metacharacters in
  server names or command args?
- **Proxy error paths** — a malicious or buggy MCP server could send
  malformed JSON-RPC, oversized payloads, or crash mid-call. The proxy
  must not crash, corrupt the DB, or leak data when this happens.

### When: public release / GitHub repo

**Trigger:** Other people install and run AgentMeter.

Tests needed:
- **bandit in CI** — static analysis catches SQL injection, hardcoded
  secrets, subprocess issues automatically on every PR. Prevents
  regressions from contributors who don't know the codebase.
- **DB file permissions** — the DB contains all tool call data. On
  multi-user systems it should not be world-readable. (Currently
  depends on umask — should be explicit.)
- **DB path traversal** — AGENTMETER_DB env var is user-controlled.
  Validate it doesn't point somewhere dangerous.

### When: multi-tenant / API keys / hosted (Month 2+)

**Trigger:** Multiple customers share infrastructure, budgets, API keys.

Tests needed:
- **Tenant isolation** — customer A cannot see customer B's data
- **Budget enforcement** — cannot exceed limits, no TOCTOU races
- **Auth/authz** — API key validation, rate limiting
- **Webhook security** — signed payloads, retry logic
- **Stripe integration** — idempotency, webhook verification

## Test Categories (detail)

### 1. Unit Tests — DB Layer (`test_db.py`)

**Positive (done):** CRUD operations with valid data, aggregations,
filtering, session naming.

**Negative (done):**
- SQL injection attempts through every string parameter
- Invalid date formats in `since` parameters
- Non-existent session IDs in queries
- Mismatched foreign keys (call referencing non-existent session)

**Boundary (done):**
- Empty strings for tool_name, server_name, session_id
- Very long strings (10K+ chars) for tool_name, arguments_json, result_json
- Unicode and special characters (emoji, null bytes, newlines) in all string fields
- Zero and negative values for elapsed_ms, result_size
- limit=0, limit=1, limit=999999
- Session with zero calls vs session with 10,000 calls
- Daily totals with days=0, days=1, days=365

### 2. Security Tests (`test_security.py`)

**Input validation (done):**
- SQL injection payloads through: tool_name, server_name, since, session_id
- Verify injected SQL is stored as literal text, not executed

**File system (done):**
- DB in deeply nested non-existent directory
- Read-only directory for DB path
- DB file permissions after creation

**Data truncation (done):**
- Result at exactly 2000 char boundary
- Very large arguments_json

**File system — deferred until public release:**
- DB path traversal (e.g. `../../etc/passwd`)
- Symlink following on DB path

**Data exposure — deferred until untrusted servers:**
- Sensitive data in arguments_json/result_json is stored (awareness test)
- Arguments truncation at proxy level (exactly 1000 chars, 1001 chars)

**Subprocess — deferred until untrusted servers:**
- Environment variable leakage to child process
- Command with shell metacharacters in server name

### 3. Boundary & Edge Case Tests (`test_boundaries.py`)

**String boundaries (done):**
- Empty string, single char, exactly at truncation limit, over truncation limit
- Null bytes in strings
- Unicode: CJK, emoji, RTL, combining characters
- Newlines and tabs in tool names

**Numeric boundaries (done):**
- elapsed_ms: 0, 1, MAX_INT
- result_size: 0, 1, very large
- total_calls: 0, 1, very large
- limit parameter: 0, negative, very large

**Time boundaries (done):**
- Dates at epoch, far future, malformed ISO strings
- Session at midnight

**Concurrency — deferred until multiple servers:**
- Two MeterDB instances writing to same file simultaneously
- Read while write is in progress

### 4. Error Condition Tests (`test_errors.py`) — deferred

**DB errors — deferred until multiple servers:**
- Corrupt DB file (write garbage bytes)
- DB locked by another process

**Proxy errors — deferred until untrusted servers:**
- Child server binary doesn't exist
- Child server crashes during initialization
- Child server crashes mid-tool-call
- Malformed JSON-RPC from child server

### 5. CLI Tests (`test_cli.py`) — done

**Positive:** Each command produces expected output with valid data.
**Negative:** Each command handles empty DB, invalid options, missing args.
**Boundary:** Edge cases in formatting (0ms, 0B, very large numbers).

### 6. Integration Tests (`test_integration.py`) — done

**Done:** Forward calls, record errors, session tracking.

**Deferred until untrusted servers:**
- Proxy handles tool that returns very large result
- Proxy handles tool that takes very long
- Multiple sequential sessions to same DB
- Session naming after various tool call patterns

## Coverage Targets

| Area | Before | Now | Target |
|------|--------|-----|--------|
| db.py query methods | Positive only | Positive + negative + boundary | Done |
| db.py writes | Positive only | Positive + security | Done |
| proxy.py | Integration only | Integration | + error paths (when untrusted servers) |
| cli.py | **0%** | Positive + negative + boundary | Done |
| models.py | Implicit | Implicit (dataclasses, no logic) | Done |

## Static Analysis

- **bandit** — add to CI before public release. Catches SQL injection,
  hardcoded secrets, subprocess issues automatically.
- **ruff** — already in use for linting
