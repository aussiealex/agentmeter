# Spec: Session Coaching (Yellow Card + Context Injection)

## Problem

Users don't know how to prompt coding agents efficiently. A well-structured
500-word prompt with file references is 10x cheaper than "fix the bug" followed
by 40 exploratory Reads. Nobody teaches this — agents happily burn tokens
exploring, and users don't see the cost until the session is over.

AgentMeter has the data to detect inefficiency in real-time. The missing piece
is a feedback path that reaches both the agent AND the user mid-session.

## Design Principle: Global, Not Per-Project

Coaching is a **cross-project feature**. It installs globally (user-level hooks
and global CLAUDE.md) and works in every session regardless of project.

The session-start flow:
1. Agent starts any session
2. Global CLAUDE.md instruction tells agent to run `agentmeter coach start`
3. Agent asks user: "Would you like cost coaching for this session?"
4. If yes → agent runs `agentmeter coach context` which examines the current
   project and outputs tailored guidance
5. If no → session proceeds normally, yellow cards still fire at thresholds
   (unless coaching is fully disabled)

This means:
- **Global hooks** — PreToolUse coach hook installed at user level, fires for
  all projects
- **Project-aware advice** — when coaching IS active, it reads the current
  project's maturity (CLAUDE.md, specs, tests, history) and gives contextual
  guidance
- **Accumulated wisdom** — cross-project data shows user-level patterns
  ("you vibe code in new projects but are efficient in mature ones")

## Discovery: Hook Output Is Invisible

Tested 2026-05-18: Claude Code silently discards PostToolUse hook stdout/stderr.
The agent never sees it. Mid-session injection via PostToolUse is impossible.

However: **PreToolUse hooks can block tool calls**, and the agent DOES see the
denial message. This is the only guaranteed injection point mid-session.

## Solution: Two Parts

### Part 1: Session Start — Set the Context

Global CLAUDE.md tells the agent to run `agentmeter coach start` at the
beginning of every session. The command:

1. Asks the user: "What kind of work? (development / personal / research)"
2. Detects project maturity from the filesystem
3. Pulls historical cost data for this project
4. Outputs tailored prompting guidance and sets session thresholds

If the user declines or skips, default thresholds still apply.

### Part 2: Yellow Card — Interrupt When It Goes Wrong

A PreToolUse hook monitors the session in real-time. When a threshold is
crossed, it blocks ONE tool call with a coaching message. The agent sees it,
tells the user what's happening and why, then retries (which passes through).

The yellow card is **project-aware** — thresholds and advice adapt based on
what `coach start` learned. A mature project with clear specs gets a tighter
leash than a greenfield exploration session.

### Layer 2: Yellow Card (active, mid-session)

A PreToolUse hook that monitors session spend/call patterns in real-time.
When a threshold is crossed, it blocks ONE tool call with a coaching message.
The agent sees the message, informs the user, and retries (which passes).

## Yellow Card Mechanics

### Trigger Conditions

Configurable thresholds (checked in order, first match fires):

| Trigger | Default | What it means |
|---------|---------|---------------|
| Call count | 50 calls | Session is getting long |
| Spend estimate | $3.00 | Real money being spent |
| Velocity spike | >10 calls/min sustained 3min | Vibe coding detected |
| Repeated tool | Same tool >15 times | Likely exploring, not executing |

Multiple thresholds can fire in a session (e.g. warning at 50 calls, again
at 100), but each threshold fires only once.

### State Management

The hook is hot-path (<5ms). It cannot do heavy analysis.

Architecture:
1. **PostToolUse hook** (existing) writes call to DB as normal, ALSO increments
   a lightweight state file: `~/.local/share/agentmeter/coach/{session_id}.json`
2. **State file** contains: `{calls: N, spend_estimate: X, last_warned_at: N, tools: {Read: 12, Edit: 3, ...}}`
3. **PreToolUse hook** reads state file, checks thresholds, decides block/pass.
   If blocking: exits non-zero with coaching message. Updates `last_warned_at`.
   If passing: exits zero immediately (no stdout needed).

State file is <1KB, single read, no DB query on the hot path.

### The Coaching Message

The blocked tool call message must serve two audiences:
- **Agent:** understand this is informational, retry will succeed
- **User (via agent):** understand the pattern and how to change behaviour

Template:
```
AgentMeter — cost checkpoint

Session: 67 calls | ~$4.20 | 38 minutes
Pattern: High-frequency reads (Read called 31 times, avg 15 lines each)
Cache: ~82% of spend (context window growing each call)

Advice for user:
  Your session is exploring broadly. If you can tell the agent exactly
  which files/functions to target, it can do in 2 calls what it's done
  in 30. Try: "In src/foo.py, the bar() function on line 120 needs X"
  instead of "fix the bug in foo."

  Alternatively, start a fresh session with a specific brief — the
  accumulated cache from this session is now the majority of cost.

This is informational. Retry your action to continue.
```

### Why Sessions Get Expensive (the quadratic problem)

The API is stateless. Every turn replays the ENTIRE conversation — system
prompt, all prior messages, all tool results — as input tokens. Turn 100
pays for everything from turns 1-99. Costs scale quadratically with
conversation length, not linearly.

Prompt caching mitigates this: the stable prefix is cached server-side and
read at 90% discount ($0.30/MTok vs $3/MTok on Sonnet). But the volume
still grows quadratically — a 449-turn session accumulates 69M cache read
tokens even with caching. Without caching, that same session would cost
~20x more.

Key facts for coaching advice:
- Cache TTL is 5 minutes (resets on each hit, so active sessions stay warm)
- Cache invalidates from any change point forward (edit, tool result injection)
- Min cacheable prefix: 1024 tokens (Sonnet), 4096 tokens (Opus)
- A fresh session resets the quadratic accumulator to zero
- Splitting a 400-call session into 4x100-call sessions saves ~60-75% on
  cache reads alone

This is why "start fresh with a brief" is the single most impactful
coaching recommendation.

### Pattern-Specific Advice

| Pattern | Detection | Coaching |
|---------|-----------|----------|
| Many small reads | Read >15x, avg input_size < 50 lines | "Reference exact files and line ranges in your prompt" |
| Repeated grep/glob | Grep or Glob >10x | "Tell the agent what you're looking for and where — don't let it search" |
| Long session, high cache | >100 LLM calls or cache >80% of spend | "Each turn replays your full history. A fresh session with a handoff brief resets this to zero — estimated saving shown." |
| High velocity | >10 calls/min for 3+ min | "Pause. Write a detailed prompt. One good instruction > 20 vague ones." |
| Edit-test loop | alternating Edit/Bash >5 cycles | "Write the full solution in one prompt with test criteria, don't iterate" |
| Broad exploration | >10 unique files read | "You're exploring. Invest 2 min writing which files matter and why." |

## Session-Start Profiling

`agentmeter coach context` generates a CLAUDE.md block by examining:

1. **Project maturity** (heuristic):
   - Has CLAUDE.md? specs/ dir? tests/ dir? CI config?
   - Score: 0 (greenfield) to 4 (fully documented)

2. **Task clarity signal** (from user, optional):
   - `agentmeter coach context --task "add export command"` → clear task
   - No flag → generic advice

3. **Historical efficiency** (from DB):
   - Average calls per session for this project
   - Average cost per session
   - Outcome rate (sessions with commits / total sessions)

4. **Output** (for CLAUDE.md injection):
```
## AgentMeter Session Context

Project maturity: high (CLAUDE.md + specs + tests)
Recent avg: 84 calls/session, $3.40/session, 72% commit rate
Efficiency tier: moderate — room to reduce calls with more specific prompts

Prompting guidance for this project:
- Project is well-documented. Reference specs and existing code by path.
- Batch related changes into single prompts with clear acceptance criteria.
- Avoid exploratory reads — state which files you need and why.
- Target: <50 calls for a focused feature, <20 for a bug fix.
```

## CLI Interface

```bash
# Session-start (agent calls this when user opts in)
agentmeter coach start                 # interactive: asks task type, outputs guidance
agentmeter coach start --task "add auth"  # skip prompt, direct context
agentmeter coach start --type personal    # personal/exploration (relaxed thresholds)
agentmeter coach start --type development # development (strict thresholds)

# Generate CLAUDE.md context block (for manual injection)
agentmeter coach context
agentmeter coach context --project myapp

# Configure yellow card thresholds
agentmeter coach set calls 50          # warn at 50 calls
agentmeter coach set spend 3.00        # warn at $3
agentmeter coach set velocity 10 180   # 10 calls/min for 180s
agentmeter coach set repeat 15         # same tool 15 times

# Show current config
agentmeter coach show

# Disable/enable (global)
agentmeter coach off
agentmeter coach on

# Post-session review (analyse last session efficiency)
agentmeter coach review
agentmeter coach review <session-id>

# Cross-project efficiency report
agentmeter coach stats                 # user-level patterns across all projects
```

## Session-Start Flow (Detail)

When the agent runs `agentmeter coach start`, the command:

1. **Detects current project** from cwd (maps to project in DB)
2. **Asks task type** (if not provided via flag):
   - `development` — building/fixing code in a known project
   - `personal` — exploration, learning, one-off scripts
   - `research` — reading/understanding, no code output expected
3. **Assesses project maturity** (filesystem heuristics):
   - CLAUDE.md exists? (+1)
   - specs/ or docs/ directory? (+1)
   - tests/ directory with >0 test files? (+1)
   - CI config (.github/workflows, etc.)? (+1)
   - Score 0-4 maps to: greenfield / early / established / mature
4. **Pulls historical data** from DB:
   - Avg calls/session for this project
   - Avg cost/session
   - Outcome rate (commit sessions / total)
   - User's overall efficiency trend
5. **Outputs tailored guidance** based on task type + maturity:

For a **mature project + development task**:
```
AgentMeter Coach — Session Context

Project: AgentMeter (maturity: 4/4 — fully documented)
Task type: development
History: avg 84 calls/session, $3.40, 72% commit rate

This project has specs, tests, and a CLAUDE.md. The agent should NOT
need to explore — everything is documented. Target for this session:

  Focused feature: <50 calls, <$2.50
  Bug fix: <20 calls, <$1.00
  Refactor: <30 calls, <$1.50

Prompting strategy:
  - Reference exact files by path and line number
  - State acceptance criteria upfront (what does "done" look like?)
  - Batch related changes: "do A, B, and C in one pass"
  - If you catch yourself saying "look at..." — stop and be specific

Yellow card will fire at: 50 calls / $3.00 / 10 calls/min sustained
```

For a **greenfield project + personal task**:
```
AgentMeter Coach — Session Context

Project: new-experiment (maturity: 0/4 — greenfield)
Task type: personal
History: first session in this project

This is exploration territory. Higher call counts are expected — you're
discovering the shape of the problem. Relaxed thresholds apply.

Prompting strategy:
  - Explore freely but notice when you're going in circles
  - Once you find what you need, summarise it before acting
  - If the session exceeds 100 calls, consider writing a brief and
    starting fresh with a focused second session

Yellow card will fire at: 100 calls / $6.00 (relaxed for exploration)
```

## Hook Installation

Coaching installs **globally** (user-level settings, not per-project). This
ensures every session across all projects gets coverage.

`agentmeter hook install --coach` adds the PreToolUse hook alongside the
existing PostToolUse metering hook:

```json
// ~/.claude/settings.json (user-level, global)
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "command": "python3 -m agentmeter.hooks.coach"
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": "python3 -m agentmeter.hook"
      }
    ]
  }
}
```

Additionally, a line is added to the user's global CLAUDE.md
(`~/.claude/CLAUDE.md`):

```markdown
## AgentMeter Coaching
At session start, ask: "Would you like cost coaching this session?"
If yes: run `agentmeter coach start` and follow its output.
If no: proceed normally. Yellow cards may still fire at spend thresholds.
```

This means:
- **Every project** gets the session-start prompt (agent asks user)
- **Every project** gets yellow card protection (PreToolUse hook)
- **Project-specific advice** comes from `coach start` reading the cwd
- **User controls opt-in** per session (coaching context is optional,
  yellow cards are always-on unless `agentmeter coach off`)

The PreToolUse coach hook:
- Reads `~/.local/share/agentmeter/coach/{session_id}.json`
- If no file or below thresholds: exit 0 (silent pass, <1ms)
- If threshold crossed and not already warned at this level: exit 1 with message
- Updates warned state
- Thresholds adjust based on task type set at session start (relaxed for
  personal/research, strict for development in mature projects)

The PostToolUse hook (existing) additionally updates the coach state file
after recording the call.

## Post-Session Review

`agentmeter coach review` analyses a completed session and outputs:

```
Session: abc123 (2026-05-19, 94 calls, $5.80, 52 min)
Outcome: 2 commits, 14 files changed, tests passing

Efficiency analysis:
  - 34 Read calls (36%) — 22 were <20 lines. Could batch.
  - 3 cycles of Edit→Bash→Edit (test-fix loops). Write tests first next time.
  - Cache reached 89% of spend by call 60. Fresh session would have saved ~$1.40.

Score: 4/10 efficiency
Potential: Same outcome achievable in ~40 calls (~$2.50) with:
  1. Start with a brief listing target files + acceptance criteria
  2. Reference line numbers instead of letting agent search
  3. Split into 2 sessions (exploration, then execution)
```

## Data Model

### Coach config (SQLite, new table)

```sql
CREATE TABLE IF NOT EXISTS coach_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Default rows:
- `enabled` → `1`
- `threshold_calls` → `50`
- `threshold_spend` → `3.00`
- `threshold_velocity_calls` → `10`
- `threshold_velocity_window` → `180`
- `threshold_repeat` → `15`

### Coach state file (filesystem, not DB — hot path)

`~/.local/share/agentmeter/coach/{session_id}.json`:

```json
{
  "calls": 67,
  "spend_estimate": 4.20,
  "started_at": "2026-05-19T10:30:00",
  "task_type": "development",
  "project_maturity": 3,
  "tools": {"Read": 31, "Edit": 8, "Bash": 15, "Grep": 7, "Glob": 6},
  "warnings_fired": [50],
  "last_tool_at": "2026-05-19T11:08:12",
  "thresholds": {"calls": 50, "spend": 3.00}
}
```

The `task_type` and `project_maturity` fields are set by `coach start` at
session beginning. If the user skips coaching, defaults apply. The
`thresholds` field stores the active thresholds for this session (may differ
from global defaults based on task type).

## Performance Budget

- PreToolUse hook: <2ms (read JSON file, compare numbers, exit)
- PostToolUse hook addition: <1ms extra (update JSON file after DB write)
- No DB queries in the PreToolUse path
- No network calls anywhere
- State file <1KB always

## Scope Boundaries

What this is:
- **Global, cross-project** cost coaching for all agent sessions
- Session-start profiling (task type + project maturity → tailored guidance)
- Mid-session yellow cards with project-aware, actionable advice
- Post-session efficiency review with improvement suggestions
- Pattern detection from tool call distribution
- Configurable thresholds that adapt to context

What this is NOT (yet):
- Automated prompt rewriting
- Model routing / tier selection
- Task estimation ("this will cost ~$X")
- Multi-user / team coaching
- Integration with external tools (Slack alerts, etc.)
- A separate product (lives inside AgentMeter, may extract later)

## Success Metrics

Measurable from AgentMeter's own data:
- Calls per session decreases after coaching is enabled
- Cost per commit decreases
- Outcome rate (sessions that produce commits) stays same or increases
- Sessions ending with no outcome decrease (less abandoned exploration)
- User opts in to coaching more over time (coaching is useful, not annoying)
- Cross-project: mature projects show fewer calls than greenfield (expected)

## The Coaching Thesis

Optimal prompt strategy depends on where you are:

| Project maturity | Task type | Expected calls | Prompt strategy |
|-----------------|-----------|----------------|-----------------|
| Greenfield (0-1) | Personal | 80-120 | Explore freely, summarise findings |
| Greenfield (0-1) | Development | 60-80 | Write a brief first, then execute |
| Established (2-3) | Development | 30-50 | Reference docs, batch changes |
| Mature (4) | Development | 15-30 | Surgical: exact files, line numbers, criteria |
| Mature (4) | Bug fix | 5-15 | Repro → fix → test, one prompt |
| Any | Research | No limit | Reading is cheap, understanding is the goal |

The coaching system teaches users to recognise where they are on this matrix
and prompt accordingly. The yellow card fires when behaviour doesn't match
the expected pattern for the context.

## Implementation Order

1. Coach state file — PostToolUse hook writes it alongside DB write
2. PreToolUse coach hook — threshold check + yellow card message
3. `agentmeter coach start` — session-start profiling (maturity + task type)
4. `agentmeter coach show/set/on/off` — config CLI
5. `agentmeter coach context` — static CLAUDE.md block generation
6. `agentmeter coach review` — post-session analysis
7. `agentmeter hook install --coach` — global hook + CLAUDE.md installation
8. Pattern-specific advice (iterate on message quality with real data)
9. `agentmeter coach stats` — cross-project user efficiency report
