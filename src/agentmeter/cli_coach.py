"""CLI commands for session coaching — review and analysis."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB
from agentmeter.heuristics import AnalysisContext, Finding, analyse_session
from agentmeter.platform import project_name
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)


@click.group()
def coach() -> None:
    """Session coaching — efficiency analysis and advice."""


@coach.command()
@click.argument("session_id", required=False)
def review(session_id: str | None) -> None:
    """Review a session for efficiency patterns.

    Analyses tool call patterns from a single session and gives
    actionable advice on how to reduce cost next time.

    If SESSION_ID is omitted, reviews the most recent session.
    Partial IDs work (e.g. first 8 characters).

    Examples:
        agentmeter coach review
        agentmeter coach review 4d44d158
    """
    db = MeterDB()

    session = _resolve_session(db, session_id)
    if not session:
        label = session_id or "any"
        click.echo(f"\n  No session found: {label}\n")
        db.close()
        return

    # Session header
    project = project_name(session.server_command)
    display = session.name or project or session.id[:8]
    started = session.started_at[:16].replace("T", " ") if session.started_at else ""

    click.echo()
    click.echo(f"  Session Review — {display}")
    click.echo(f"  {started}")
    click.echo(f"  {'─' * 55}")

    # Get tool call stats for this session
    stats = _session_tool_stats(db, session.id)
    total_calls = sum(stats.values())

    if total_calls == 0:
        click.echo("  No tool calls recorded for this session.")
        click.echo()
        db.close()
        return

    # Cost data
    cost_str = ""
    session_cost = _get_session_cost(db, session)
    if session_cost is not None:
        cost_str = f"  ${session_cost:.2f}"

    # Tool breakdown
    click.echo(f"  {total_calls} tool calls{cost_str}")
    click.echo()
    for tool, count in sorted(stats.items(), key=lambda x: -x[1]):
        pct = count / total_calls * 100
        bar_len = int(pct / 100 * 20)
        bar = "█" * bar_len
        click.echo(f"    {tool:<20}  {bar}  {count:>4} ({pct:.0f}%)")

    # Outcome
    if session.outcome:
        click.echo()
        parts = []
        if session.commits:
            parts.append(f"{session.commits} commits")
        if session.files_changed:
            parts.append(f"{session.files_changed} files changed")
        if session.tests_passed:
            parts.append(f"{session.tests_passed} tests passed")
        if session.tests_failed:
            parts.append(f"{session.tests_failed} tests failed")
        click.echo(f"  Outcome: {', '.join(parts) if parts else session.outcome}")

    # Run heuristics
    ctx = AnalysisContext(conn=db._conn, session_id=session.id)
    findings = analyse_session(ctx)

    # Score
    score = _efficiency_score(findings, total_calls)
    click.echo()
    click.echo(f"  Efficiency: {score}/10")

    # Findings
    if findings:
        _print_review_findings(findings)
    else:
        click.echo()
        click.echo("  No patterns detected — clean session.")

    click.echo()
    db.close()


def _resolve_session(db: MeterDB, session_id: str | None):
    """Find session by partial ID, name, or most recent."""
    sessions = db.get_sessions(limit=100)
    if not sessions:
        return None

    if not session_id:
        return sessions[0]  # most recent

    for s in sessions:
        if (s.id == session_id
                or s.id.startswith(session_id)
                or (s.name and s.name == session_id)):
            return s
    return None


def _session_tool_stats(db: MeterDB, session_id: str) -> dict[str, int]:
    """Get {tool_name: count} for a specific session."""
    rows = db._conn.execute(
        "SELECT tool_name, COUNT(*) as cnt "
        "FROM tool_call WHERE session_id = ? "
        "GROUP BY tool_name ORDER BY cnt DESC",
        (session_id,),
    ).fetchall()
    return {r["tool_name"]: r["cnt"] for r in rows}


def _efficiency_score(findings: list[Finding], total_calls: int) -> int:
    """Score from 1-10 based on findings severity and count."""
    if not findings:
        return 10

    penalty = 0
    for f in findings:
        if f.severity == "critical":
            penalty += 3
        elif f.severity == "warning":
            penalty += 1.5
        else:
            penalty += 0.5

    # Scale penalty — more calls means more tolerance
    if total_calls > 100:
        penalty *= 0.8
    elif total_calls < 30:
        penalty *= 1.2

    score = max(1, min(10, round(10 - penalty)))
    return score


def _get_session_cost(db: MeterDB, session) -> float | None:
    """Get real token cost for a session, or None if unavailable."""
    jsonl_path = find_session_jsonl(session.id, session.server_command)
    if not jsonl_path:
        return None
    tokens = read_session_tokens_from_file(jsonl_path)
    if not tokens or tokens.llm_call_count == 0:
        return None
    rate = db.get_rate(tokens.model_id)
    if not rate:
        return None
    cost_data = calculate_session_cost(tokens, rate)
    return cost_data.total_cost


def _print_review_findings(findings: list[Finding]) -> None:
    """Print findings grouped by severity."""
    click.echo()
    click.echo("  Patterns detected:")

    for f in findings:
        if f.severity == "critical":
            marker = "!!"
        elif f.severity == "warning":
            marker = " !"
        else:
            marker = "  "

        click.echo(f"  {marker} {f.summary}")
        click.echo(f"  {' ' * len(marker)} {f.advice}")
