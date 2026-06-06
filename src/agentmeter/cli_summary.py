"""Summary command — compact cost context for agent injection."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
from agentmeter.heuristics import AnalysisContext, Finding, analyse_cross_session
from agentmeter.platform import project_name
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)


@click.command()
@click.option(
    "--days", "-d", default=7,
    help="Days of history to summarise.",
)
@click.option(
    "--project", "-p", default=None,
    help="Filter to a specific project.",
)
@click.option(
    "--directives/--no-directives", default=True,
    help="Include coaching directives from heuristics.",
)
def summary(days: int, project: str | None, directives: bool) -> None:
    """Output a compact cost summary for agent context injection.

    Designed to be read by AI agents at session start. Output is
    plain text, not formatted for humans. Pipe into CLAUDE.md or
    use in a session-start hook.

    \b
    Usage:
        agentmeter summary >> CLAUDE.md
        agentmeter summary --project AgentMeter
    """
    db = MeterDB()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    sessions = db.get_sessions(limit=500)

    # Gather per-session data
    entries: list[_Entry] = []
    for s in sessions:
        if s.started_at < since:
            continue
        proj = project_name(s.server_command)
        if project and proj != project:
            continue

        cost = 0.0
        llm_calls = 0
        jsonl_path = find_session_jsonl(
            s.id, s.server_command,
        )
        if jsonl_path:
            tokens = read_session_tokens_from_file(jsonl_path)
            if tokens and tokens.llm_call_count > 0:
                rate = db.get_rate(tokens.model_id)
                if rate:
                    cost_data = calculate_session_cost(
                        tokens, rate,
                    )
                    cost = cost_data.total_cost
                    llm_calls = tokens.llm_call_count

        entries.append(_Entry(
            project=proj, cost=cost,
            llm_calls=llm_calls, commits=s.commits,
            tests_passed=s.tests_passed,
            tests_failed=s.tests_failed,
        ))

    # Run heuristics for directives before closing DB
    findings: list[Finding] = []
    if directives:
        ctx = AnalysisContext(
            conn=db._conn,
            since=since,
            project=project,
        )
        findings = analyse_cross_session(ctx)

    db.close()

    if not entries:
        click.echo("No session data available.")
        return

    _print_summary(entries, days, project)

    if directives and findings:
        _print_directives(findings)


class _Entry:
    __slots__ = (
        "project", "cost", "llm_calls",
        "commits", "tests_passed", "tests_failed",
    )

    def __init__(
        self, *, project: str, cost: float,
        llm_calls: int, commits: int,
        tests_passed: int, tests_failed: int,
    ) -> None:
        self.project = project
        self.cost = cost
        self.llm_calls = llm_calls
        self.commits = commits
        self.tests_passed = tests_passed
        self.tests_failed = tests_failed


def _print_summary(
    entries: list[_Entry], days: int,
    project: str | None,
) -> None:
    total_cost = sum(e.cost for e in entries)
    total_sessions = len(entries)
    total_llm_calls = sum(e.llm_calls for e in entries)
    total_commits = sum(e.commits for e in entries)

    sessions_with_cost = [e for e in entries if e.cost > 0]
    avg_cost = (
        total_cost / len(sessions_with_cost)
        if sessions_with_cost else 0
    )
    avg_llm_calls = (
        total_llm_calls / len(sessions_with_cost)
        if sessions_with_cost else 0
    )
    cost_per_call = (
        total_cost / total_llm_calls
        if total_llm_calls > 0 else 0
    )
    cost_per_commit = (
        total_cost / total_commits
        if total_commits > 0 else 0
    )

    scope = project or "all projects"
    click.echo(f"# AgentMeter Cost Context ({scope}, "
               f"last {days} days)")
    click.echo("#")
    click.echo(
        f"# {total_sessions} sessions, "
        f"${total_cost:.2f} total, "
        f"${avg_cost:.2f}/session avg",
    )
    click.echo(
        f"# {avg_llm_calls:.0f} LLM calls/session avg, "
        f"${cost_per_call:.4f}/call",
    )
    if total_commits > 0:
        click.echo(
            f"# {total_commits} commits, "
            f"${cost_per_commit:.2f}/commit",
        )

    # Session length warning
    long_sessions = [
        e for e in entries if e.llm_calls > 150
    ]
    if long_sessions:
        long_avg = sum(
            e.cost for e in long_sessions
        ) / len(long_sessions)
        short_sessions = [
            e for e in sessions_with_cost if e.llm_calls <= 150
        ]
        short_avg = (
            sum(e.cost for e in short_sessions)
            / len(short_sessions)
        ) if short_sessions else 0
        if short_avg > 0:
            ratio = long_avg / short_avg
            click.echo("#")
            click.echo(
                f"# Sessions over 150 LLM calls cost "
                f"{ratio:.1f}x more than shorter ones.",
            )
            click.echo(
                f"# (${long_avg:.2f} vs ${short_avg:.2f} avg). "
                f"Consider splitting large tasks.",
            )

    # Cost trend
    if len(sessions_with_cost) >= 4:
        mid = len(sessions_with_cost) // 2
        # Sessions are newest-first from DB
        recent = sessions_with_cost[:mid]
        older = sessions_with_cost[mid:]
        recent_avg = sum(e.cost for e in recent) / len(recent)
        older_avg = sum(e.cost for e in older) / len(older)
        if older_avg > 0:
            change = (recent_avg - older_avg) / older_avg * 100
            if abs(change) > 10:
                direction = "up" if change > 0 else "down"
                click.echo("#")
                click.echo(
                    f"# Cost trend: {direction} "
                    f"{abs(change):.0f}% vs prior period.",
                )

    # Per-project breakdown (top 5)
    proj_costs: dict[str, float] = {}
    for e in entries:
        proj_costs[e.project] = (
            proj_costs.get(e.project, 0) + e.cost
        )
    if len(proj_costs) > 1:
        click.echo("#")
        click.echo("# By project:")
        sorted_projs = sorted(
            proj_costs.items(), key=lambda x: -x[1],
        )
        for name, cost in sorted_projs[:5]:
            pct = (cost / total_cost * 100) if total_cost else 0
            click.echo(f"#   {name}: ${cost:.2f} ({pct:.0f}%)")


def _finding_to_directive(f: Finding) -> str | None:
    """Convert a heuristic Finding into a short imperative directive."""
    d = f.data
    if f.pattern == "repeated_file_cross_session":
        name = d.get("display_name", d.get("file", "?"))
        if d.get("is_written") or d.get("is_volatile"):
            return f"Read {name} once per session, not repeatedly."
        return f"Don't re-read {name} — inline or summarise it."

    if f.pattern == "binary_image_reads":
        name = d.get("file", "?").rsplit("/", 1)[-1]
        return f"Describe {name} in text instead of reading the image."

    if f.pattern == "session_size_outlier":
        avg = d.get("project_avg", 0)
        if avg > 0:
            target = max(int(avg * 1.5), 60)
            return f"Keep sessions under {target} tool calls."
        return None

    if f.pattern == "project_concentration":
        proj = d.get("project", "?")
        pct = d.get("ratio", 0)
        return (
            f"{proj} is {pct:.0%} of all agent time — "
            f"check this matches your priorities."
        )

    return None


def _print_directives(findings: list[Finding]) -> None:
    """Emit up to 3 imperative coaching directives from findings."""
    directives: list[str] = []
    for f in findings:
        d = _finding_to_directive(f)
        if d and d not in directives:
            directives.append(d)
        if len(directives) >= 3:
            break

    if not directives:
        return

    click.echo("#")
    click.echo("# Directives:")
    for d in directives:
        click.echo(f"#   - {d}")
