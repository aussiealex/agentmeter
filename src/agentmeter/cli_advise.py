"""CLI command for spend advisory — the credit pool advisor."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
from agentmeter.heuristics import AnalysisContext, analyse_cross_session
from agentmeter.models import SessionTokens
from agentmeter.platform import project_name
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)


@click.command()
@click.option(
    "--days", "-d", default=7,
    help="Days of history to analyse (default: 7).",
)
@click.option(
    "--project", "-p", default=None,
    help="Filter to a project (substring match).",
)
def advise(days: int, project: str | None) -> None:
    """Analyse spending patterns and recommend optimisations.

    Reads real token data from recent sessions and identifies:
    - Where your spend actually goes (cache reads vs output vs input)
    - Which projects cost the most
    - Session length vs cost relationship
    - Tool call pattern analysis with actionable recommendations

    Examples:
        agentmeter advise
        agentmeter advise -p djbaby
        agentmeter advise --days 14
    """
    db = MeterDB()
    sessions = db.get_sessions(limit=200)

    if not sessions:
        click.echo("\n  No sessions recorded.\n")
        db.close()
        return

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Resolve project filter (substring, case-insensitive)
    resolved_project = None
    if project:
        resolved_project = _resolve_project(db, project)
        if not resolved_project:
            click.echo(f"\n  No project matching '{project}'.\n")
            db.close()
            return

    # Gather data from all sessions in range
    entries: list[_SessionEntry] = []
    for session in sessions:
        if session.started_at < cutoff:
            continue

        proj = project_name(session.server_command)
        if resolved_project and proj != resolved_project:
            continue

        jsonl_path = find_session_jsonl(session.id, session.server_command)
        if not jsonl_path:
            continue

        tokens = read_session_tokens_from_file(jsonl_path)
        if not tokens or tokens.llm_call_count == 0:
            continue

        rate = db.get_rate(tokens.model_id)
        if not rate:
            continue

        cost_data = calculate_session_cost(tokens, rate)

        # Get tool call count for this session
        stats = db.get_session_stats(limit=200)
        tool_calls = 0
        for s in stats:
            if s.session_id == session.id:
                tool_calls = s.total_calls
                break

        entries.append(_SessionEntry(
            session_id=session.id,
            project=proj,
            tokens=tokens,
            total_cost=cost_data.total_cost,
            cache_read_cost=cost_data.cache_read_cost,
            output_cost=cost_data.output_cost,
            input_cost=cost_data.input_cost + cost_data.cache_create_cost,
            tool_calls=tool_calls,
        ))

    if not entries:
        label = f" for '{resolved_project}'" if resolved_project else ""
        click.echo(f"\n  No cost data{label} for the last {days} days.\n")
        db.close()
        return

    # Header
    scope = resolved_project or "all projects"
    click.echo()
    click.echo(f"  Spend Analysis — {scope} (last {days} days)")
    click.echo(f"  {'─' * 55}")

    # Analyse and display
    _print_spend_breakdown(entries)
    if not resolved_project:
        _print_project_breakdown(entries)
    _print_session_analysis(entries)
    _print_recommendations(entries, days)

    # Tool call pattern analysis
    heuristic_ctx = AnalysisContext(
        conn=db._conn, since=cutoff, project=resolved_project,
    )
    findings = analyse_cross_session(heuristic_ctx)
    _print_findings(findings)

    db.close()


def _resolve_project(db: MeterDB, query: str) -> str | None:
    """Find a project by case-insensitive substring match."""
    rows = db._conn.execute(
        "SELECT DISTINCT project FROM tool_call "
        "WHERE project != '' ORDER BY project",
    ).fetchall()
    query_lower = query.lower()
    for r in rows:
        if query_lower in r["project"].lower():
            return r["project"]
    return None


class _SessionEntry:
    """Lightweight container for analysis — not a model."""

    def __init__(
        self, *, session_id: str, project: str,
        tokens: SessionTokens, total_cost: float,
        cache_read_cost: float, output_cost: float,
        input_cost: float, tool_calls: int,
    ) -> None:
        self.session_id = session_id
        self.project = project
        self.tokens = tokens
        self.total_cost = total_cost
        self.cache_read_cost = cache_read_cost
        self.output_cost = output_cost
        self.input_cost = input_cost
        self.tool_calls = tool_calls


def _print_spend_breakdown(entries: list[_SessionEntry]) -> None:
    total = sum(e.total_cost for e in entries)
    cache = sum(e.cache_read_cost for e in entries)
    output = sum(e.output_cost for e in entries)
    inp = sum(e.input_cost for e in entries)

    cache_pct = (cache / total * 100) if total > 0 else 0
    output_pct = (output / total * 100) if total > 0 else 0
    input_pct = (inp / total * 100) if total > 0 else 0

    click.echo(f"  Total:        ${total:>10.2f}  across {len(entries)} sessions")
    click.echo()
    click.echo("  Where it goes:")
    click.echo(
        f"    Cache reads:  ${cache:>10.2f}  ({cache_pct:.0f}%)"
        f"  — conversation history re-sent each turn"
    )
    click.echo(
        f"    Input/create: ${inp:>10.2f}  ({input_pct:.0f}%)"
        f"  — new content + cache writes"
    )
    click.echo(
        f"    Output:       ${output:>10.2f}  ({output_pct:.0f}%)"
        f"  — Claude's responses"
    )


def _print_project_breakdown(entries: list[_SessionEntry]) -> None:
    projects: dict[str, float] = {}
    for e in entries:
        projects[e.project] = projects.get(e.project, 0) + e.total_cost

    sorted_projects = sorted(
        projects.items(), key=lambda x: -x[1],
    )

    total = sum(v for _, v in sorted_projects)

    click.echo()
    click.echo("  By project:")
    for project, cost in sorted_projects:
        pct = (cost / total * 100) if total > 0 else 0
        bar_len = int(pct / 100 * 25)
        bar = "█" * bar_len
        click.echo(
            f"    {project:<20}  {bar}  ${cost:>8.2f}  ({pct:.0f}%)"
        )


def _print_session_analysis(entries: list[_SessionEntry]) -> None:
    if len(entries) < 2:
        return

    # Sort by cost to find outliers
    by_cost = sorted(entries, key=lambda e: -e.total_cost)
    avg_cost = sum(e.total_cost for e in entries) / len(entries)
    avg_calls = sum(e.tokens.llm_call_count for e in entries) / len(entries)

    click.echo()
    click.echo("  Session patterns:")
    click.echo(f"    Average session: {avg_calls:.0f} LLM calls, ${avg_cost:.2f}")

    # Most expensive
    top = by_cost[0]
    click.echo(
        f"    Most expensive:  {top.tokens.llm_call_count} LLM calls, "
        f"${top.total_cost:.2f} ({top.project})"
    )

    # Cheapest
    bottom = by_cost[-1]
    click.echo(
        f"    Cheapest:        {bottom.tokens.llm_call_count} LLM calls, "
        f"${bottom.total_cost:.2f} ({bottom.project})"
    )

    # Cost per LLM call
    total_calls = sum(e.tokens.llm_call_count for e in entries)
    total_cost = sum(e.total_cost for e in entries)
    if total_calls > 0:
        cost_per_call = total_cost / total_calls
        click.echo(f"    Cost per LLM call: ${cost_per_call:.4f}")


def _print_recommendations(
    entries: list[_SessionEntry], days: int,
) -> None:
    total = sum(e.total_cost for e in entries)
    cache = sum(e.cache_read_cost for e in entries)
    cache_pct = (cache / total * 100) if total > 0 else 0

    recommendations: list[str] = []

    # 1. Cache dominance
    if cache_pct > 80:
        savings = cache * 0.3  # estimate 30% savings from shorter sessions
        recommendations.append(
            f"Cache reads are {cache_pct:.0f}% of spend. Shorter sessions "
            f"reduce cache re-sends. Splitting long sessions could save "
            f"~${savings:.0f} over {days} days."
        )

    # 2. Long sessions
    long_sessions = [
        e for e in entries if e.tokens.llm_call_count > 200
    ]
    if long_sessions:
        long_cost = sum(e.total_cost for e in long_sessions)
        long_pct = (long_cost / total * 100) if total > 0 else 0
        n = len(long_sessions)
        word = "session" if n == 1 else "sessions"
        recommendations.append(
            f"{n} {word} exceeded 200 LLM calls, "
            f"accounting for ${long_cost:.2f} ({long_pct:.0f}% of spend). "
            f"Consider breaking large tasks into focused sessions."
        )

    # 3. Project concentration
    projects: dict[str, float] = {}
    for e in entries:
        projects[e.project] = projects.get(e.project, 0) + e.total_cost
    top_project = max(projects.items(), key=lambda x: x[1])
    top_pct = (top_project[1] / total * 100) if total > 0 else 0
    if top_pct > 60 and len(projects) > 1:
        recommendations.append(
            f"{top_project[0]} is {top_pct:.0f}% of total spend "
            f"(${top_project[1]:.2f}). If this isn't your top priority, "
            f"your agent time doesn't match your stated priorities."
        )

    # 4. Output token ratio
    total_output = sum(e.tokens.output_tokens for e in entries)
    total_tokens = sum(
        e.tokens.input_tokens + e.tokens.cache_creation_tokens
        + e.tokens.cache_read_tokens + e.tokens.output_tokens
        for e in entries
    )
    output_ratio = (total_output / total_tokens * 100) if total_tokens > 0 else 0
    if output_ratio < 1:
        recommendations.append(
            f"Output tokens are only {output_ratio:.1f}% of total. "
            f"Most spend is re-reading context, not generating. "
            f"CLAUDE.md and system prompt size directly affect every turn."
        )

    if recommendations:
        click.echo()
        click.echo("  Recommendations:")
        for i, rec in enumerate(recommendations, 1):
            click.echo(f"    {i}. {rec}")


_PATTERN_GROUPS = [
    (
        "Repeated Files",
        "Files re-read across sessions — each is a tool call + context inflation",
        ["repeated_file_cross_session"],
    ),
    (
        "Large Reads",
        "Oversized results pumped into context",
        ["binary_image_reads", "large_result_read"],
    ),
    (
        "Session Outliers",
        "Sessions with disproportionate call counts",
        ["session_size_outlier"],
    ),
    (
        "Spend Distribution",
        "How agent time is allocated",
        ["project_concentration"],
    ),
]

_MAX_DETAIL = 5


def _print_findings(findings: list) -> None:
    if not findings:
        click.echo()
        return

    by_pattern: dict[str, list] = {}
    for f in findings:
        by_pattern.setdefault(f.pattern, []).append(f)

    click.echo()
    click.echo("  Tool Call Patterns")
    click.echo(f"  {'─' * 55}")

    printed_any = False
    for group_name, group_desc, patterns in _PATTERN_GROUPS:
        group_findings = []
        for p in patterns:
            group_findings.extend(by_pattern.get(p, []))

        if not group_findings:
            continue

        printed_any = True

        if group_name == "Repeated Files":
            _print_repeated_files(group_findings)
        else:
            _print_detail_group(group_name, group_desc, group_findings)

    if not printed_any:
        click.echo()
        click.echo("  No patterns detected.")

    click.echo()


def _print_repeated_files(findings: list) -> None:
    """Group repeated files by action type."""
    total_reads = sum(f.data.get("total_reads", 0) for f in findings)

    click.echo()
    click.echo(
        f"  Repeated Files ({len(findings)} files, "
        f"~{total_reads} redundant reads)"
    )

    # Classify into action groups
    groups: dict[str, list] = {
        "inline": [],
        "summarise": [],
        "path": [],
        "image": [],
    }

    for f in findings:
        advice = f.advice.lower()
        if "inline the whole file" in advice:
            groups["inline"].append(f)
        elif "design intent" in advice:
            groups["image"].append(f)
        elif "also modifies" in advice:
            groups["path"].append(f)
        else:
            groups["summarise"].append(f)

    group_meta = [
        (
            "inline",
            "Inline into CLAUDE.md",
            "Small and stable — paste the whole file into CLAUDE.md.",
        ),
        (
            "summarise",
            "Summarise in CLAUDE.md",
            "Stable but too large to inline — add a summary of the key content.",
        ),
        (
            "path",
            "Add path to CLAUDE.md",
            "Agent edits these files — add the path so it reads once, not searches.",
        ),
        (
            "image",
            "Replace with text description",
            "Binary images inflate ~33% in context. Describe the design in text.",
        ),
    ]

    for key, title, desc in group_meta:
        items = groups[key]
        if not items:
            continue

        group_reads = sum(f.data.get("total_reads", 0) for f in items)
        click.echo()
        click.echo(f"  {title} ({len(items)} files, {group_reads} reads):")
        click.echo(f"  {desc}")

        for f in items:
            name = f.data.get("display_name", "")
            if not name:
                name = f.summary.split(" read ")[0]
            reads = f.data.get("total_reads", 0)
            sessions = f.data.get("session_count", 0)
            click.echo(f"    {name} ({reads}x / {sessions} sessions)")


def _print_detail_group(
    name: str, desc: str, findings: list,
) -> None:
    """Group findings by advice, compact format."""
    click.echo()
    click.echo(f"  {name} ({len(findings)} found)")
    click.echo(f"  {desc}")

    # Group by advice text
    by_advice: dict[str, list] = {}
    for f in findings:
        by_advice.setdefault(f.advice, []).append(f)

    for advice, items in by_advice.items():
        click.echo()
        click.echo(f"  {advice}")
        for f in items:
            # Strip the advice from summary since it's in the header
            click.echo(f"    {f.summary}")
