"""CLI command for spend forecasting."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)


@click.command()
@click.option(
    "--days", "-d", default=7,
    help="Days of history to base forecast on (default: 7).",
)
@click.option(
    "--project", "-p", default=None,
    help="Filter by project name.",
)
def forecast(days: int, project: str | None) -> None:
    """Forecast monthly spend from recent session costs.

    Reads real token data from recent Claude Code sessions, computes
    a daily average, and projects to 30 days.

    Examples:
        agentmeter forecast
        agentmeter forecast --days 14
    """
    db = MeterDB()

    sessions = db.get_sessions(limit=200)
    if not sessions:
        click.echo("\n  No sessions recorded.\n")
        db.close()
        return

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Gather daily costs from real token data
    daily_costs: dict[str, float] = {}
    session_count = 0

    from agentmeter.platform import project_name

    for session in sessions:
        if session.started_at < cutoff:
            continue
        if project:
            proj = project_name(session.server_command)
            if project.lower() not in proj.lower():
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
        day = session.started_at[:10]
        daily_costs[day] = daily_costs.get(day, 0) + cost_data.total_cost
        session_count += 1

    db.close()

    if not daily_costs:
        click.echo("\n  No cost data available for the last "
                   f"{days} days.\n")
        return

    # Calculate stats
    sorted_days = sorted(daily_costs.items())
    total_spend = sum(v for _, v in sorted_days)
    active_days = len(sorted_days)
    avg_daily = total_spend / active_days
    projected_monthly = avg_daily * 30

    # Trend: compare first half vs second half
    if active_days >= 2:
        mid = active_days // 2
        first_half_avg = (
            sum(v for _, v in sorted_days[:mid]) / mid
        )
        second_half_avg = (
            sum(v for _, v in sorted_days[mid:]) / (active_days - mid)
        )
        if first_half_avg > 0:
            trend_pct = (
                (second_half_avg - first_half_avg) / first_half_avg * 100
            )
        else:
            trend_pct = 0.0
    else:
        trend_pct = 0.0

    # Display
    click.echo()
    click.echo(f"  Spend Forecast (based on last {days} days)")
    click.echo(f"  {'─' * 55}")
    click.echo()

    # Daily bar chart
    max_cost = max(v for _, v in sorted_days) if sorted_days else 1
    for day, day_cost in sorted_days:
        bar_len = int((day_cost / max_cost) * 25) if max_cost > 0 else 0
        bar = "█" * bar_len
        click.echo(f"  {day}  {bar}  ${day_cost:>8.2f}")

    click.echo()
    click.echo(f"  {'─' * 55}")
    click.echo(f"  Sessions:          {session_count}")
    click.echo(f"  Active days:       {active_days} / {days}")
    click.echo(f"  Total spend:       ${total_spend:>10.2f}")
    click.echo(f"  Daily average:     ${avg_daily:>10.2f}")
    click.echo(
        f"  Projected monthly: ${projected_monthly:>10.2f}"
    )

    if active_days >= 2:
        direction = "increasing" if trend_pct > 5 else (
            "decreasing" if trend_pct < -5 else "stable"
        )
        click.echo(
            f"  Trend:             {direction} "
            f"({trend_pct:+.0f}%)"
        )

    click.echo()
    click.echo("  * Costs at API rates. Subscription users pay flat rate.")
    click.echo()
