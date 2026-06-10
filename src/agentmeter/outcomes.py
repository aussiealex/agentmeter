"""Session outcome detection from tool call data.

Scans a session's tool calls to detect:
- Git commits (from Bash calls containing 'git commit')
- Files changed (from Bash calls containing 'git diff' output)
- Test results (from Bash calls containing pytest output)
- Lint results (from Bash calls containing ruff/eslint/flake8 output)
- Build results (from Bash calls containing build/compile output)
- Retry loops (same command repeated 3+ times)

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
    tests_failed, lint_passes, lint_errors, retries,
    errors, total_calls.
    """
    calls = db.get_calls_for_export(session_id=session_id)

    commits = 0
    files_changed = 0
    tests_passed = 0
    tests_failed = 0
    lint_passes = 0
    lint_errors = 0
    retries = 0
    errors = 0
    total_calls = len(calls)

    # Track repeated commands for retry detection
    recent_cmds: list[str] = []

    for call in calls:
        if call.is_error:
            errors += 1

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

        # Detect lint results
        lp, le = _parse_lint_results(args, result, call.is_error)
        lint_passes += lp
        lint_errors += le

        # Track retries (same command run 3+ times)
        cmd_key = _normalise_cmd(args)
        recent_cmds.append(cmd_key)
        if len(recent_cmds) > 10:
            recent_cmds.pop(0)
        if recent_cmds.count(cmd_key) >= 3:
            retries += 1

    return {
        "commits": commits,
        "files_changed": files_changed,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "lint_passes": lint_passes,
        "lint_errors": lint_errors,
        "retries": retries,
        "errors": errors,
        "total_calls": total_calls,
    }


def detect_and_store(db: MeterDB, session_id: str) -> None:
    """Detect outcomes and write them to the session row."""
    facts = detect_session_outcome(db, session_id)
    # Core outcome fields (always present in schema)
    db.update_session_outcome(
        session_id,
        commits=facts["commits"],
        files_changed=facts["files_changed"],
        tests_passed=facts["tests_passed"],
        tests_failed=facts["tests_failed"],
    )
    # Extended quality fields (additive migration)
    db.update_session_quality(
        session_id,
        lint_passes=facts["lint_passes"],
        lint_errors=facts["lint_errors"],
        retries=facts["retries"],
        errors=facts["errors"],
        total_calls=facts["total_calls"],
    )


def backfill_outcomes(db: MeterDB, limit: int = 500) -> int:
    """Backfill outcomes for sessions that have no outcome data."""
    all_sessions = db.get_sessions(limit=limit)
    updated = 0
    for s in all_sessions:
        if s.commits > 0 or s.tests_passed > 0 or s.tests_failed > 0:
            continue
        facts = detect_session_outcome(db, s.id)
        if any(v > 0 for v in facts.values()):
            detect_and_store(db, s.id)
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


# Lint tool patterns
_LINT_TOOLS = re.compile(
    r"\b(ruff|eslint|flake8|pylint|mypy|tsc)\b",
)
_LINT_CLEAN_RE = re.compile(
    r"All checks passed|no issues found|0 errors|"
    r"Found 0 error|no problems found",
    re.IGNORECASE,
)
_LINT_ERROR_RE = re.compile(
    r"Found \d+ error|error:|Error:|FAILED|"
    r"\d+ problems?\b|\d+ errors?\b",
)


def _parse_lint_results(
    args: str, result: str, is_error: bool,
) -> tuple[int, int]:
    """Detect lint/typecheck passes and failures."""
    if not _LINT_TOOLS.search(args):
        return 0, 0
    if is_error:
        return 0, 1
    if _LINT_CLEAN_RE.search(result):
        return 1, 0
    if _LINT_ERROR_RE.search(result):
        return 0, 1
    # Ran a lint tool with no clear error signal → likely clean
    return 1, 0


def _normalise_cmd(args: str) -> str:
    """Normalise a command string for retry detection.

    Strips whitespace differences so minor reformats don't
    prevent matching.
    """
    return " ".join(args.split())
