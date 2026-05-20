"""CLI for AgentMeter — wrap MCP servers and view stats."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.cli_breaker import breaker
from agentmeter.cli_budget import budget
from agentmeter.cli_format import format_ms, print_distribution, print_tool_table
from agentmeter.cli_hook import hook
from agentmeter.cli_rates import rates
from agentmeter.db import MeterDB


@click.group()
@click.version_option(package_name="agentmeter")
def main() -> None:
    """AgentMeter — meter every MCP tool call."""


main.add_command(budget)
main.add_command(breaker)
main.add_command(hook)
main.add_command(rates)


def _load_pro_commands() -> None:
    """Register pro commands if agentmeter-pro is installed."""
    try:
        from agentmeter.cli_advise import advise
        from agentmeter.cli_cost import cost
        from agentmeter.cli_dashboard import dashboard
        from agentmeter.cli_export import export
        from agentmeter.cli_forecast import forecast
        from agentmeter.cli_strategy import strategy
        from agentmeter.cli_summary import summary

        main.add_command(advise)
        main.add_command(cost)
        main.add_command(dashboard)
        main.add_command(export)
        main.add_command(forecast)
        main.add_command(strategy)
        main.add_command(summary)
    except ImportError:
        pass


_load_pro_commands()


@main.command(context_settings={"ignore_unknown_options": True})
@click.argument("command")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option("--name", "-n", default="", help="Human-readable name for this server.")
def wrap(command: str, args: tuple[str, ...], name: str) -> None:
    """Wrap an MCP server command, proxying and metering all tool calls.

    Example:
        agentmeter wrap python -m mailsift.mcp.server
        agentmeter wrap --name mailsift python -m mailsift.mcp.server
    """
    from agentmeter.proxy import run_proxy

    run_proxy(command=command, args=list(args), server_name=name)


@main.command()
@click.option("--today", "period", flag_value="today", help="Show today's stats.")
@click.option("--week", "period", flag_value="week", help="Show this week's stats.")
@click.option("--all", "period", flag_value="all", help="Show all-time stats.")
@click.option("--server", "-s", default=None, help="Filter by server name.")
@click.option(
    "--distribution", is_flag=True, default=False,
    help="Show per-server session percentiles (p50/p90/p99).",
)
def stats(period: str | None, server: str | None, distribution: bool) -> None:
    """Show aggregated tool call statistics."""
    db = MeterDB()

    if distribution:
        print_distribution(db, server)
        db.close()
        return

    since = None
    if period == "today":
        since = datetime.now().strftime("%Y-%m-%d")
    elif period == "week":
        since = (datetime.now() - timedelta(days=7)).isoformat()
    elif period is None:
        # Default to today
        since = datetime.now().strftime("%Y-%m-%d")

    tool_stats = db.get_tool_stats(since=since, server_name=server)
    total_calls = sum(t.call_count for t in tool_stats)
    total_errors = sum(t.error_count for t in tool_stats)
    total_time_ms = sum(t.total_elapsed_ms for t in tool_stats)

    period_label = period or "today"
    click.echo()
    click.echo(f"  AgentMeter Stats ({period_label})")
    click.echo(f"  {'─' * 60}")

    if not tool_stats:
        click.echo("  No tool calls recorded.")
        click.echo()
        db.close()
        return

    click.echo(
        f"  Total: {total_calls} calls | "
        f"{total_errors} errors | "
        f"{format_ms(total_time_ms)} tool time"
    )
    click.echo()

    print_tool_table(tool_stats, total_calls)
    click.echo()

    db.close()


@main.command()
@click.option("--limit", "-l", default=10, help="Number of sessions to show.")
def sessions(limit: int) -> None:
    """Show recent sessions with tool breakdowns and outcomes."""
    db = MeterDB()
    session_stats = db.get_session_stats(limit=limit)

    if not session_stats:
        click.echo("\n  No sessions recorded.\n")
        db.close()
        return

    # Build outcome lookup from session table
    raw_sessions = db.get_sessions(limit=limit * 2)
    outcomes = {s.id: s for s in raw_sessions}

    for ss in session_stats:
        click.echo()
        click.echo(f"  Session: {ss.session_name} ({ss.server_name})")
        click.echo(f"  Started: {ss.started_at}")
        click.echo(f"  {'─' * 50}")

        if ss.tools:
            print_tool_table(ss.tools, ss.total_calls)
        else:
            click.echo("  No tool calls in this session.")

        summary = (
            f"  Total: {ss.total_calls} calls | "
            f"{ss.total_errors} errors | "
            f"{format_ms(ss.total_elapsed_ms)}"
        )

        # Append outcome if available
        s = outcomes.get(ss.session_id)
        if s and s.outcome:
            parts = []
            if s.commits:
                parts.append(f"{s.commits} commits")
            if s.files_changed:
                parts.append(f"{s.files_changed} files")
            if s.tests_passed:
                parts.append(f"{s.tests_passed} passed")
            if s.tests_failed:
                parts.append(f"{s.tests_failed} failed")
            if parts:
                summary += f" | {', '.join(parts)}"

        click.echo(summary)

    click.echo()
    db.close()


@main.command()
def backfill() -> None:
    """Backfill session outcomes from historical tool call data.

    Scans Bash calls for git commits and test results, then
    updates session rows with the detected outcomes.
    """
    from agentmeter.outcomes import backfill_outcomes

    db = MeterDB()
    updated = backfill_outcomes(db)
    db.close()
    click.echo(f"  Updated {updated} sessions with outcome data.")


@main.command()
@click.option("--days", "-d", default=7, help="Number of days to show.")
def daily(days: int) -> None:
    """Show daily call totals with cost when available."""
    db = MeterDB()
    totals = db.get_daily_totals(days=days)

    if not totals:
        click.echo("\n  No data for this period.\n")
        db.close()
        return

    # Build daily cost map from real token data (pro feature)
    daily_costs: dict[str, float] = {}
    try:
        from agentmeter.session_reader import (
            calculate_session_cost,
            find_session_jsonl,
            read_session_tokens_from_file,
        )

        sessions = db.get_sessions(limit=200)
        for session in sessions:
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
    except ImportError:
        pass

    has_costs = bool(daily_costs)

    click.echo()
    click.echo(f"  Daily Totals (last {days} days)")
    click.echo(f"  {'─' * 60}")

    max_calls = max(t.call_count for t in totals) if totals else 1

    for t in totals:
        bar_len = int((t.call_count / max_calls) * 25) if max_calls > 0 else 0
        bar = "█" * bar_len
        err_str = f" ({t.error_count} err)" if t.error_count else ""
        cost_str = ""
        if has_costs and t.day in daily_costs:
            cost_str = f"  ${daily_costs[t.day]:>8.2f}"
        click.echo(
            f"  {t.day}  {bar}  {t.call_count:>4} calls{err_str}{cost_str}"
        )

    if has_costs:
        total_cost = sum(daily_costs.values())
        click.echo(f"  {'─' * 60}")
        click.echo(f"  {'Total cost (API rates)':>46}  ${total_cost:>8.2f}")

    click.echo()
    db.close()


@main.command()
@click.argument("session_id")
@click.argument("name")
def rename(session_id: str, name: str) -> None:
    """Rename a session.

    Example:
        agentmeter rename a3f8b2c1d4e5 "debugging email search"
    """
    db = MeterDB()
    if db.rename_session(session_id, name):
        click.echo(f"  Renamed to: {name}")
    else:
        click.echo(f"  Session not found: {session_id}")
    db.close()


@main.command()
@click.option("--limit", "-l", default=20, help="Number of calls to show.")
@click.option("--tool", "-t", default=None, help="Filter by tool name.")
def calls(limit: int, tool: str | None) -> None:
    """Show recent individual tool calls."""
    db = MeterDB()
    from agentmeter.cli_format import format_bytes
    recent = db.get_recent_calls(limit=limit, tool_name=tool)

    if not recent:
        click.echo("\n  No tool calls recorded.\n")
        db.close()
        return

    click.echo()
    click.echo(f"  Recent Tool Calls{f' ({tool})' if tool else ''}")
    click.echo(f"  {'─' * 70}")

    for c in recent:
        status = "ERR" if c.is_error else "OK "
        time_str = c.started_at[11:19] if len(c.started_at) > 19 else c.started_at
        click.echo(
            f"  {time_str}  {status}  {c.tool_name:<30}  "
            f"{c.elapsed_ms:>6}ms  {format_bytes(c.result_size):>8}"
        )

    click.echo()
    db.close()


if __name__ == "__main__":
    main()
