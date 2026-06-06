"""CLI command for model tier analysis and comparison."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
from agentmeter.models import RateCard, SessionTokens
from agentmeter.platform import project_name
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)


@click.command()
@click.option(
    "--days", "-d", default=7,
    help="Days of history to analyse.",
)
@click.option(
    "--project", "-p", default=None,
    help="Filter by project name.",
)
def model(days: int, project: str | None) -> None:
    """Analyse model tier usage and show savings from alternatives.

    Shows which model your sessions ran on, what they cost, and what
    they would have cost on cheaper tiers. Helps decide when to use
    Opus vs Sonnet vs Haiku.

    \b
    Examples:
        agentmeter model
        agentmeter model --days 30
        agentmeter model -p AgentMeter
    """
    db = MeterDB()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    sessions = db.get_sessions(limit=500)

    # Gather per-model session data
    model_data: dict[str, _ModelBucket] = {}

    for s in sessions:
        if s.started_at < cutoff:
            continue
        if project:
            proj = project_name(s.server_command)
            if project.lower() not in proj.lower():
                continue

        jsonl_path = find_session_jsonl(s.id, s.server_command)
        if not jsonl_path:
            continue
        tokens = read_session_tokens_from_file(jsonl_path)
        if not tokens or tokens.llm_call_count == 0:
            continue

        mid = tokens.model_id or "unknown"
        rate = db.get_rate(mid)
        if not rate:
            continue

        cost = calculate_session_cost(tokens, rate)

        bucket = model_data.setdefault(mid, _ModelBucket(mid))
        bucket.sessions += 1
        bucket.total_cost += cost.total_cost
        bucket.total_llm_calls += tokens.llm_call_count
        bucket.token_sets.append(tokens)

    all_rates = db.get_all_rates()
    db.close()

    if not model_data:
        click.echo("\n  No session data with model info.\n")
        return

    # Display current usage
    click.echo()
    scope = project or "all projects"
    click.echo(f"  Model Tier Analysis ({scope}, last {days} days)")
    click.echo(f"  {'─' * 58}")

    total_cost = sum(b.total_cost for b in model_data.values())

    for mid, bucket in sorted(
        model_data.items(), key=lambda x: -x[1].total_cost,
    ):
        pct = bucket.total_cost / total_cost * 100 if total_cost else 0
        avg = bucket.total_cost / bucket.sessions
        click.echo()
        click.echo(
            f"  {mid}  —  "
            f"{bucket.sessions} sessions, "
            f"${bucket.total_cost:.2f} ({pct:.0f}%)",
        )
        click.echo(
            f"    avg ${avg:.2f}/session, "
            f"{bucket.total_llm_calls / bucket.sessions:.0f} "
            f"LLM calls/session",
        )

    # "What if" comparison
    if total_cost > 0:
        _print_comparisons(model_data, all_rates, total_cost)

    click.echo()


class _ModelBucket:
    __slots__ = (
        "model_id", "sessions", "total_cost",
        "total_llm_calls", "token_sets",
    )

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.sessions = 0
        self.total_cost = 0.0
        self.total_llm_calls = 0
        self.token_sets: list[SessionTokens] = []


def _print_comparisons(
    model_data: dict[str, _ModelBucket],
    all_rates: list[RateCard],
    total_cost: float,
) -> None:
    """Show what the same sessions would cost on different tiers."""
    rate_map = {r.model_id: r for r in all_rates}

    # For each model used, show what alternatives would cost
    comparisons: list[tuple[str, str, float, float]] = []

    for mid, bucket in model_data.items():
        current_rate = rate_map.get(mid)
        if not current_rate:
            continue

        # Compare against same-vendor cheaper tiers
        alternatives = _get_alternatives(mid, all_rates)
        for alt_rate in alternatives:
            alt_cost = 0.0
            for tokens in bucket.token_sets:
                c = calculate_session_cost(tokens, alt_rate)
                alt_cost += c.total_cost

            saving = bucket.total_cost - alt_cost
            if saving > 0.01:
                comparisons.append((
                    mid, alt_rate.model_id,
                    alt_cost, saving,
                ))

    if not comparisons:
        return

    click.echo()
    click.echo(f"  {'─' * 58}")
    click.echo("  What-if: same sessions on cheaper tiers")
    click.echo()

    for current, alt, alt_cost, saving in sorted(
        comparisons, key=lambda x: -x[3],
    ):
        pct = saving / total_cost * 100
        click.echo(
            f"    {current} -> {alt}:  "
            f"${alt_cost:.2f} (save ${saving:.2f}, {pct:.0f}%)",
        )

    # Summary advice
    best = max(comparisons, key=lambda x: x[3])
    click.echo()
    click.echo(
        f"  Biggest opportunity: switch {best[0]} to {best[1]} "
        f"for ${best[3]:.2f} savings ({best[3]/total_cost*100:.0f}%)",
    )

    # Caveat
    click.echo()
    click.echo(
        "  * Cheaper models may need more calls to achieve the "
        "same result.",
    )
    click.echo(
        "    Real savings depend on task complexity and model "
        "capability.",
    )


def _get_alternatives(
    model_id: str, all_rates: list[RateCard],
) -> list[RateCard]:
    """Get cheaper models from the same vendor family."""
    # Determine vendor prefix
    if model_id.startswith("claude-"):
        prefix = "claude-"
    elif model_id.startswith("gemini-"):
        prefix = "gemini-"
    elif model_id.startswith("gpt-") or model_id.startswith("o"):
        prefix = None  # OpenAI has mixed naming
        openai_ids = {
            "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini",
        }
    else:
        return []

    current_rate = None
    candidates = []
    for r in all_rates:
        if r.model_id == model_id:
            current_rate = r
            continue
        if (
            (prefix and r.model_id.startswith(prefix))
            or (prefix is None and r.model_id in openai_ids)
        ):
            candidates.append(r)

    if not current_rate:
        return []

    # Only return cheaper alternatives
    return [
        r for r in candidates
        if r.output_per_mtok < current_rate.output_per_mtok
    ]
