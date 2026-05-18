"""Session outcome detection from tool call data.

Scans a session's tool calls to detect:
- Git commits (from Bash calls containing 'git commit')
- Files changed (from Bash calls containing 'git diff' output)
- Test results (from Bash calls containing pytest output)

Facts are stored on the session row. The outcome label is derived
at query time from the facts — consistent with the "store facts,
derive economics" principle.
"""

from __future__ import annotations

import re

from agentmeter.db import MeterDB


def detect_session_outcome(
    db: MeterDB, session_id: str,
) -> dict[str, int]:
    """Scan a session's tool calls and return outcome facts.

    Returns dict with: commits, files_changed, tests_passed,
    tests_failed.
    """
    calls = db.get_calls_for_export(session_id=session_id)

    commits = 0
    files_changed = 0
    tests_passed = 0
    tests_failed = 0

    for call in calls:
        if call.tool_name != "Bash":
            continue

        args = call.arguments_json
        result = call.result_json

        # Detect git commits
        if "git commit" in args and not call.is_error:
            commits += _count_commits(result)

        # Detect files changed from git output
        files_changed += _count_files_changed(result)

        # Detect test results
        tp, tf = _parse_test_results(result)
        tests_passed += tp
        tests_failed += tf

    return {
        "commits": commits,
        "files_changed": files_changed,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
    }


def detect_and_store(db: MeterDB, session_id: str) -> None:
    """Detect outcomes and write them to the session row."""
    facts = detect_session_outcome(db, session_id)
    db.update_session_outcome(session_id, **facts)


def backfill_outcomes(db: MeterDB, limit: int = 500) -> int:
    """Backfill outcomes for sessions that have no outcome data."""
    sessions = db.get_sessions(limit=limit)
    updated = 0
    for s in sessions:
        if s.commits > 0 or s.tests_passed > 0 or s.tests_failed > 0:
            continue
        facts = detect_session_outcome(db, s.id)
        if any(v > 0 for v in facts.values()):
            db.update_session_outcome(s.id, **facts)
            updated += 1
    return updated


# ── Parsing helpers ──────────────────────────────────────────

# Matches: [main abc1234] commit message
_COMMIT_RE = re.compile(
    r"\[[\w/.-]+ [0-9a-f]+\]",
)

# Matches: 3 files changed, 10 insertions(+), 2 deletions(-)
_FILES_CHANGED_RE = re.compile(
    r"(\d+) files? changed",
)

# Matches pytest summary: "245 passed" or "3 failed"
_PYTEST_PASSED_RE = re.compile(
    r"(\d+) passed",
)
_PYTEST_FAILED_RE = re.compile(
    r"(\d+) failed",
)


def _count_commits(result: str) -> int:
    """Count git commit confirmations in command output."""
    return len(_COMMIT_RE.findall(result))


def _count_files_changed(result: str) -> int:
    """Extract files changed count from git diff/commit output."""
    total = 0
    for m in _FILES_CHANGED_RE.finditer(result):
        total += int(m.group(1))
    return total


def _parse_test_results(result: str) -> tuple[int, int]:
    """Extract passed/failed counts from pytest output."""
    passed = 0
    failed = 0
    for m in _PYTEST_PASSED_RE.finditer(result):
        passed = max(passed, int(m.group(1)))
    for m in _PYTEST_FAILED_RE.finditer(result):
        failed = max(failed, int(m.group(1)))
    return passed, failed
