"""CLI commands for budget management."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB
from agentmeter.models import Budget


@click.group()
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
