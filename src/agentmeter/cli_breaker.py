"""CLI commands for circuit breaker management."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB
from agentmeter.models import BreakerConfig


@click.group()
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
                f"  {t.tripped_at[:19]}  "
                f"{t.server_name}  "
                f"{t.call_count} calls/"
                f"{t.window_seconds}s"
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
