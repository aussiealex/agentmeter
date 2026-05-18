"""CLI commands for rate card management."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB
from agentmeter.db.schema import _seed_default_rates
from agentmeter.models import RateCard


@click.group()
def rates() -> None:
    """View and manage model pricing for cost estimation."""


@rates.command("show")
def rates_show() -> None:
    """Show all model rates.

    Example:
        agentmeter rates show
    """
    db = MeterDB()
    all_rates = db.get_all_rates()

    if not all_rates:
        click.echo("\n  No rates configured. Run: agentmeter rates reset\n")
        db.close()
        return

    click.echo()
    click.echo(
        f"  {'Model':<22}  {'Input':>8}  {'Output':>8}  "
        f"{'Cached':>8}  {'Cal.':>5}"
    )
    click.echo(f"  {'─' * 60}")

    for r in all_rates:
        click.echo(
            f"  {r.display_name or r.model_id:<22}  "
            f"${r.input_per_mtok:>6.2f}  "
            f"${r.output_per_mtok:>6.2f}  "
            f"${r.cached_per_mtok:>6.2f}  "
            f"{r.calibration_factor:>5.2f}"
        )

    click.echo()
    click.echo("  Rates are $ per million tokens. Cal. = calibration factor.")
    click.echo()
    db.close()


@rates.command("set")
@click.argument("model_id")
@click.argument("input_rate", type=float)
@click.argument("output_rate", type=float)
@click.option(
    "--cached", "-c", type=float, default=None,
    help="Cached input rate (default: 10% of input rate).",
)
@click.option(
    "--name", "-n", default=None,
    help="Display name for the model.",
)
def rates_set(
    model_id: str,
    input_rate: float,
    output_rate: float,
    cached: float | None,
    name: str | None,
) -> None:
    """Set or update a model rate.

    Rates are $ per million tokens.

    Examples:
        agentmeter rates set claude-opus-4-6 15.0 75.0
        agentmeter rates set gemini-2.5-pro 1.25 10.0 --cached 0.315
        agentmeter rates set custom-model 5.0 20.0 --name "My Model"
    """
    db = MeterDB()

    cached_rate = cached if cached is not None else input_rate * 0.1

    rate = RateCard(
        model_id=model_id,
        display_name=name or model_id,
        input_per_mtok=input_rate,
        output_per_mtok=output_rate,
        cached_per_mtok=cached_rate,
    )
    db.set_rate(rate)

    click.echo(
        f"  Rate set: {model_id}  "
        f"${input_rate}/Mtok in, "
        f"${output_rate}/Mtok out, "
        f"${cached_rate}/Mtok cached"
    )
    db.close()


@rates.command("reset")
@click.confirmation_option(prompt="Reset all rates to defaults?")
def rates_reset() -> None:
    """Reset rate card to built-in defaults."""
    db = MeterDB()
    db.clear_rates()
    _seed_default_rates(db._conn)
    db._conn.commit()

    all_rates = db.get_all_rates()
    click.echo(f"  Reset to {len(all_rates)} default rates.")
    db.close()
