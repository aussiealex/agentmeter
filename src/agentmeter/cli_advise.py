"""CLI command for spend advisory — the credit pool advisor."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
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
def advise(days: int) -> None:
    """Analyse spending patterns and recommend optimisations.

    Reads real token data from recent sessions and identifies:
    - Where your spend actually goes (cache reads vs output vs input)
    - Which projects cost the most
    - Session length vs cost relationship
    - Actionable recommendations to reduce spend

    Examples:
        agentmeter advise
        agentmeter advise --days 14
    """
    db = MeterDB()
    sessions = db.get_sessions(limit=200)

    if not sessions:
        click.echo("\n  No sessions recorded.\n")
        db.close()
        return

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Gather data from all sessions in range
    entries: list[_SessionEntry] = []
    for session in sessions:
        if session.started_at < cutoff:
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
        project = project_name(session.server_command)

        # Get tool call count for this session
        stats = db.get_session_stats(limit=200)
        tool_calls = 0
        for s in stats:
            if s.session_id == session.id:
                tool_calls = s.total_calls
                break

        entries.append(_SessionEntry(
            session_id=session.id,
            project=project,
            tokens=tokens,
            total_cost=cost_data.total_cost,
            cache_read_cost=cost_data.cache_read_cost,
            output_cost=cost_data.output_cost,
            input_cost=cost_data.input_cost + cost_data.cache_create_cost,
            tool_calls=tool_calls,
        ))

    db.close()

    if not entries:
        click.echo(f"\n  No cost data for the last {days} days.\n")
        return

    # Analyse and display
    _print_spend_breakdown(entries, days)
    _print_project_breakdown(entries)
    _print_session_analysis(entries)
    _print_recommendations(entries, days)


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


def _print_spend_breakdown(entries: list[_SessionEntry], days: int) -> None:
    total = sum(e.total_cost for e in entries)
    cache = sum(e.cache_read_cost for e in entries)
    output = sum(e.output_cost for e in entries)
    inp = sum(e.input_cost for e in entries)

    cache_pct = (cache / total * 100) if total > 0 else 0
    output_pct = (output / total * 100) if total > 0 else 0
    input_pct = (inp / total * 100) if total > 0 else 0

    click.echo()
    click.echo(f"  Spend Analysis (last {days} days)")
    click.echo(f"  {'─' * 55}")
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
        recommendations.append(
            f"{len(long_sessions)} sessions exceeded 200 LLM calls, "
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

    click.echo()
