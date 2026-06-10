"""CLI command for value multiplier reporting.

Shows whether agent sessions delivered value exceeding their cost.
Combines real token cost data with session outcomes to compute
a value multiplier: estimated_dev_time_saved / session_cost.

Heuristics for estimated dev time:
  - 1 commit ≈ 20 min of dev work
  - 1 test pass ≈ 2 min (writing + running)
  - 1 file changed ≈ 10 min
  - 1 lint pass ≈ 3 min (config + fix cycle)
These are conservative — real savings are likely higher.
"""

from __future__ import annotations

import click

from agentmeter.db import MeterDB
from agentmeter.platform import project_name

# Minutes of developer time saved per outcome unit
_MINUTES_PER_COMMIT = 20
_MINUTES_PER_TEST = 2
_MINUTES_PER_FILE = 10
_MINUTES_PER_LINT = 3

# Default developer hourly rate (USD)
_DEFAULT_HOURLY_RATE = 150


def estimate_dev_minutes(
    commits: int,
    tests_passed: int,
    files_changed: int,
    lint_passes: int,
) -> float:
    """Estimate minutes of developer time the agent saved."""
    return (
        commits * _MINUTES_PER_COMMIT
        + tests_passed * _MINUTES_PER_TEST
        + files_changed * _MINUTES_PER_FILE
        + lint_passes * _MINUTES_PER_LINT
    )


def estimate_dev_value(
    minutes: float,
    hourly_rate: float = _DEFAULT_HOURLY_RATE,
) -> float:
    """Convert estimated dev minutes to dollar value."""
    return minutes / 60 * hourly_rate


def quality_score(
    errors: int,
    total_calls: int,
    tests_failed: int,
    retries: int,
    lint_errors: int,
) -> int:
    """Compute a 0-100 quality score for a session.

    Starts at 100, deducts for:
      - Error rate (up to -40)
      - Test failures (up to -25)
      - Retries/loops (up to -20)
      - Lint errors (up to -15)
    """
    score = 100

    # Error rate penalty
    if total_calls > 0:
        error_rate = errors / total_calls
        score -= min(40, int(error_rate * 200))

    # Test failure penalty
    if tests_failed > 0:
        score -= min(25, tests_failed * 5)

    # Retry penalty
    score -= min(20, retries * 4)

    # Lint error penalty
    score -= min(15, lint_errors * 5)

    return max(0, score)


@click.command()
@click.option(
    "--limit", "-l", default=10,
    help="Number of recent sessions to show.",
)
@click.option(
    "--project", "-p", default=None,
    help="Filter by project name.",
)
@click.option(
    "--rate", "-r", default=_DEFAULT_HOURLY_RATE, type=float,
    help=f"Developer hourly rate in USD (default: ${_DEFAULT_HOURLY_RATE}).",
)
def value(limit: int, project: str | None, rate: float) -> None:
    """Show value multiplier for recent sessions.

    Compares agent cost against estimated developer time saved.
    A multiplier >1x means the agent saved more than it cost.

    Examples:
        agentmeter value
        agentmeter value -p AgentMeter
        agentmeter value --rate 200
    """
    from agentmeter.session_reader import (
        calculate_session_cost,
        find_session_jsonl,
        read_session_tokens_from_file,
    )

    db = MeterDB()
    all_sessions = db.get_sessions(
        limit=limit * 3 if project else limit,
    )

    if not all_sessions:
        click.echo("\n  No sessions recorded.\n")
        db.close()
        return

    click.echo()
    click.echo(f"  Value Report  (dev rate: ${rate:.0f}/hr)")
    click.echo(f"  {'─' * 72}")

    shown = 0
    totals = {"cost": 0.0, "value": 0.0, "minutes": 0.0}

    for session in all_sessions:
        if shown >= limit:
            break

        proj = project_name(session.server_command)
        if project and project.lower() not in proj.lower():
            continue

        # Get real cost
        jsonl_path = find_session_jsonl(
            session.id, session.server_command,
        )
        session_cost = 0.0
        if jsonl_path:
            tokens = read_session_tokens_from_file(jsonl_path)
            if tokens and tokens.llm_call_count > 0:
                rate_card = db.get_rate(tokens.model_id)
                if rate_card:
                    cost_data = calculate_session_cost(tokens, rate_card)
                    session_cost = cost_data.total_cost

        # Estimate value
        minutes = estimate_dev_minutes(
            session.commits,
            session.tests_passed,
            session.files_changed,
            session.lint_passes,
        )
        dev_value = estimate_dev_value(minutes, rate)

        # Quality score
        q_score = quality_score(
            session.errors,
            session.total_calls,
            session.tests_failed,
            session.retries,
            session.lint_errors,
        )

        # Multiplier
        if session_cost > 0:
            multiplier = dev_value / session_cost
            mult_str = f"{multiplier:.1f}x"
        elif dev_value > 0:
            mult_str = "∞"
        else:
            mult_str = "—"

        # Quality indicator
        if q_score >= 80:
            q_label = "good"
        elif q_score >= 50:
            q_label = "fair"
        else:
            q_label = "poor"

        # Output parts
        outcome_parts = []
        if session.commits:
            outcome_parts.append(f"{session.commits}c")
        if session.tests_passed:
            outcome_parts.append(f"{session.tests_passed}t")
        if session.files_changed:
            outcome_parts.append(f"{session.files_changed}f")
        if session.lint_passes:
            outcome_parts.append(f"{session.lint_passes}l")
        outcomes_str = " ".join(outcome_parts) if outcome_parts else "—"

        cost_str = f"${session_cost:.2f}" if session_cost > 0 else "—"
        value_str = f"${dev_value:.2f}" if dev_value > 0 else "—"
        time_str = f"{minutes:.0f}m" if minutes > 0 else "—"

        click.echo(
            f"  {proj:<22} "
            f"cost {cost_str:>8}  "
            f"value {value_str:>8}  "
            f"mult {mult_str:>5}  "
            f"quality {q_score:>3} ({q_label})"
        )
        click.echo(
            f"  {'':22} "
            f"time {time_str:>8}  "
            f"outcomes {outcomes_str}"
        )

        totals["cost"] += session_cost
        totals["value"] += dev_value
        totals["minutes"] += minutes
        shown += 1

    if shown == 0:
        click.echo("  No matching sessions found.")
        click.echo()
        db.close()
        return

    # Summary
    click.echo(f"  {'─' * 72}")
    total_mult = (
        f"{totals['value'] / totals['cost']:.1f}x"
        if totals["cost"] > 0 else "—"
    )
    click.echo(
        f"  {'Total':<22} "
        f"cost ${totals['cost']:>7.2f}  "
        f"value ${totals['value']:>7.2f}  "
        f"mult {total_mult:>5}  "
        f"time {totals['minutes']:.0f}m saved"
    )
    click.echo()

    db.close()
