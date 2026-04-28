"""CLI for AgentMeter — wrap MCP servers and view stats."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
from agentmeter.models import BreakerConfig, Budget


@click.group()
@click.version_option(package_name="agentmeter")
def main() -> None:
    """AgentMeter — meter every MCP tool call."""


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
        _print_distribution(db, server)
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
        f"{_format_ms(total_time_ms)} tool time"
    )
    click.echo()

    # Tool breakdown
    _print_tool_table(tool_stats, total_calls)
    click.echo()

    db.close()


@main.command()
@click.option("--limit", "-l", default=10, help="Number of sessions to show.")
def sessions(limit: int) -> None:
    """Show recent sessions with tool breakdowns."""
    db = MeterDB()
    session_stats = db.get_session_stats(limit=limit)

    if not session_stats:
        click.echo("\n  No sessions recorded.\n")
        db.close()
        return

    for ss in session_stats:
        click.echo()
        click.echo(f"  Session: {ss.session_name} ({ss.server_name})")
        click.echo(f"  Started: {ss.started_at}")
        click.echo(f"  {'─' * 50}")

        if ss.tools:
            _print_tool_table(ss.tools, ss.total_calls)
        else:
            click.echo("  No tool calls in this session.")

        click.echo(
            f"  Total: {ss.total_calls} calls | "
            f"{ss.total_errors} errors | "
            f"{_format_ms(ss.total_elapsed_ms)}"
        )

    click.echo()
    db.close()


@main.command()
@click.option("--days", "-d", default=7, help="Number of days to show.")
def daily(days: int) -> None:
    """Show daily call totals."""
    db = MeterDB()
    totals = db.get_daily_totals(days=days)

    if not totals:
        click.echo("\n  No data for this period.\n")
        db.close()
        return

    click.echo()
    click.echo(f"  Daily Totals (last {days} days)")
    click.echo(f"  {'─' * 50}")

    max_calls = max(t["call_count"] for t in totals) if totals else 1

    for t in totals:
        bar_len = int((t["call_count"] / max_calls) * 30) if max_calls > 0 else 0
        bar = "█" * bar_len
        err_str = f" ({t['error_count']} err)" if t["error_count"] else ""
        click.echo(
            f"  {t['day']}  {bar}  {t['call_count']} calls{err_str}  "
            f"{_format_ms(t['total_elapsed_ms'])}"
        )

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
            f"{c.elapsed_ms:>6}ms  {_format_bytes(c.result_size):>8}"
        )

    click.echo()
    db.close()


@main.group()
def budget() -> None:
    """Manage budget rules for tool call limits."""


@budget.command("set")
@click.argument("scope", type=click.Choice(["session", "daily"]))
@click.argument("max_calls", type=int)
@click.option(
    "--server", "-s", default="",
    help="Limit to a specific server (default: all).",
)
@click.option(
    "--action", "-a",
    type=click.Choice(["deny", "warn"]),
    default="deny",
    help="Action when limit is reached: deny (block) or warn (log only).",
)
def budget_set(scope: str, max_calls: int, server: str, action: str) -> None:
    """Set a budget rule.

    Examples:
        agentmeter budget set session 50
        agentmeter budget set daily 200 --server mailsift
        agentmeter budget set session 100 --action warn
    """
    db = MeterDB()
    b = Budget(scope=scope, server_name=server, max_calls=max_calls, action=action)
    db.set_budget(b)

    target = server or "all servers"
    click.echo(
        f"  Budget set: {scope} limit of {max_calls} calls "
        f"for {target} ({action})"
    )
    db.close()


@budget.command("show")
def budget_show() -> None:
    """Show all budget rules."""
    db = MeterDB()
    budgets = db.get_budgets()

    if not budgets:
        click.echo("\n  No budget rules configured.\n")
        db.close()
        return

    click.echo()
    click.echo("  Budget Rules")
    click.echo(f"  {'─' * 60}")

    for b in budgets:
        target = b.server_name or "all servers"
        click.echo(
            f"  [{b.action.upper():>4}]  {b.scope:<8}  "
            f"{b.max_calls:>6} calls  {target}"
        )

    click.echo()
    db.close()


@budget.command("clear")
@click.option("--scope", "-s", type=click.Choice(["session", "daily"]), default=None)
@click.option("--server", default=None, help="Clear rules for a specific server.")
@click.confirmation_option(prompt="Remove budget rules?")
def budget_clear(scope: str | None, server: str | None) -> None:
    """Remove budget rules.

    Examples:
        agentmeter budget clear            # clear all
        agentmeter budget clear -s daily   # clear daily rules only
    """
    db = MeterDB()
    removed = db.clear_budget(scope=scope, server_name=server)
    click.echo(f"  Removed {removed} budget rule(s).")
    db.close()


@main.group()
def breaker() -> None:
    """Manage circuit breakers for velocity-based call gating."""


@breaker.command("set")
@click.argument("max_calls", type=int)
@click.argument("window", type=int)
@click.option(
    "--cooldown", "-c", default=300,
    help="Seconds to block after trip (default: 300).",
)
@click.option(
    "--server", "-s", default="",
    help="Limit to a specific server (default: all).",
)
def breaker_set(
    max_calls: int, window: int, cooldown: int, server: str,
) -> None:
    """Set a circuit breaker.

    Trips when MAX_CALLS occur within WINDOW seconds.
    Once tripped, blocks all calls for --cooldown seconds.

    Examples:
        agentmeter breaker set 20 60
        agentmeter breaker set 10 30 --cooldown 600
        agentmeter breaker set 50 120 --server mailsift
    """
    db = MeterDB()
    config = BreakerConfig(
        server_name=server,
        max_calls=max_calls,
        window_seconds=window,
        cooldown_seconds=cooldown,
    )
    db.set_breaker(config)

    target = server or "all servers"
    click.echo(
        f"  Breaker set: {max_calls} calls/{window}s "
        f"for {target} (cooldown: {cooldown}s)"
    )
    db.close()


@breaker.command("show")
def breaker_show() -> None:
    """Show circuit breaker configs and recent trips."""
    db = MeterDB()
    configs = db.get_breakers()

    if not configs:
        click.echo("\n  No circuit breakers configured.\n")
        db.close()
        return

    click.echo()
    click.echo("  Circuit Breakers")
    click.echo(f"  {'─' * 60}")

    for c in configs:
        target = c.server_name or "all servers"
        click.echo(
            f"  {c.max_calls} calls/{c.window_seconds}s  "
            f"cooldown: {c.cooldown_seconds}s  {target}"
        )

    trips = db.get_breaker_trips(limit=5)
    if trips:
        click.echo()
        click.echo("  Recent Trips")
        click.echo(f"  {'─' * 60}")
        for t in trips:
            click.echo(
                f"  {t['tripped_at'][:19]}  "
                f"{t['server_name']}  "
                f"{t['call_count']} calls/"
                f"{t['window_seconds']}s"
            )

    click.echo()
    db.close()


@breaker.command("clear")
@click.option(
    "--server", default=None,
    help="Clear breaker for a specific server.",
)
@click.confirmation_option(prompt="Remove circuit breakers?")
def breaker_clear(server: str | None) -> None:
    """Remove circuit breaker configs."""
    db = MeterDB()
    removed = db.clear_breakers(server_name=server)
    click.echo(f"  Removed {removed} circuit breaker(s).")
    db.close()


# ── Helpers ─────────────────────────────────────────────────────────


def _print_distribution(db: MeterDB, server: str | None) -> None:
    """Print per-server session distribution (p50/p90/p99)."""
    dists = db.get_session_distribution(server_name=server)

    if not dists:
        click.echo("\n  No sessions recorded.\n")
        return

    click.echo()
    click.echo("  Session Distribution (all time)")
    click.echo(f"  {'─' * 70}")

    for d in dists:
        label = d.server_name or "(unnamed)"
        click.echo(f"\n  {label}  ({d.session_count} sessions)")
        click.echo(f"  {'─' * 50}")
        click.echo(
            f"  {'':>18}  {'p50':>10}  {'p90':>10}  {'p99':>10}"
        )
        click.echo(
            f"  {'calls':>18}  {d.p50_calls:>10}  "
            f"{d.p90_calls:>10}  {d.p99_calls:>10}"
        )
        click.echo(
            f"  {'tool time':>18}  {_format_ms(d.p50_elapsed_ms):>10}  "
            f"{_format_ms(d.p90_elapsed_ms):>10}  {_format_ms(d.p99_elapsed_ms):>10}"
        )
        rb = (d.p50_result_bytes, d.p90_result_bytes, d.p99_result_bytes)
        click.echo(
            f"  {'result size':>18}  {_format_bytes(rb[0]):>10}  "
            f"{_format_bytes(rb[1]):>10}  {_format_bytes(rb[2]):>10}"
        )

    click.echo()


def _print_tool_table(tools: list, total_calls: int) -> None:
    """Print a formatted tool stats table with bar chart."""
    max_count = max(t.call_count for t in tools) if tools else 1

    for t in tools:
        bar_len = int((t.call_count / max_count) * 20) if max_count > 0 else 0
        bar = "█" * bar_len
        err_str = f" ({t.error_count} err)" if t.error_count else ""
        avg_str = f"{t.avg_elapsed_ms:.0f}ms avg" if t.avg_elapsed_ms else ""

        click.echo(
            f"  {t.tool_name:<30}  {bar}  "
            f"{t.call_count:>4} calls{err_str}  {avg_str}"
        )


def _format_ms(ms: int) -> str:
    """Format milliseconds into human-readable duration."""
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


def _format_bytes(size: int) -> str:
    """Format byte count into human-readable size."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


if __name__ == "__main__":
    main()
