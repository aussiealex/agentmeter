"""CLI commands for cost analysis using real token data."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)


@click.command()
@click.argument("session_id", required=False)
@click.option(
    "--limit", "-l", default=5,
    help="Number of recent sessions to show if no session_id given.",
)
def cost(session_id: str | None, limit: int) -> None:
    """Show real token usage and cost for a session.

    If SESSION_ID is given, shows detailed cost for that session.
    If omitted, shows cost summary for recent sessions.

    Reads real token data from Claude Code's session transcript files.
    Requires Claude Code sessions — other agents use byte-based estimates.

    Examples:
        agentmeter cost
        agentmeter cost 9a91d336-9fd7-4a53-af6d-636e753c942d
    """
    db = MeterDB()

    if session_id:
        _show_session_cost(db, session_id)
    else:
        _show_recent_costs(db, limit)

    db.close()


def _show_session_cost(db: MeterDB, session_id: str) -> None:
    """Show detailed cost breakdown for a single session."""
    # Find the session in our DB
    sessions = db.get_sessions(limit=100)
    session = None
    for s in sessions:
        if (s.id == session_id
                or s.id.startswith(session_id)
                or (s.name and s.name == session_id)):
            session = s
            break

    if not session:
        click.echo(f"  Session not found: {session_id}")
        return

    # Find and read the JSONL
    jsonl_path = find_session_jsonl(session.id, session.server_command)
    if not jsonl_path:
        click.echo(f"  No session transcript found for: {session.id}")
        click.echo("  (Only Claude Code sessions have token data on disk)")
        return

    tokens = read_session_tokens_from_file(jsonl_path)
    if not tokens or tokens.llm_call_count == 0:
        click.echo(f"  No token data in transcript for: {session.id}")
        return

    # Get rate card for this model
    rate = db.get_rate(tokens.model_id)
    if not rate:
        click.echo(f"  No rate card for model: {tokens.model_id}")
        click.echo("  Run: agentmeter rates to see available models")
        return

    cost_data = calculate_session_cost(tokens, rate)

    # Get tool call stats for context
    session_stats = db.get_session_stats(limit=100)
    ss = None
    for s in session_stats:
        if s.session_id == session.id:
            ss = s
            break

    # Display
    project = session.server_command.rstrip("/").rsplit("/", 1)[-1]
    click.echo()
    click.echo(f"  Session: {session.name or session.id}")
    click.echo(f"  Project: {project}")
    click.echo(f"  Model:   {tokens.model_id}")
    click.echo(f"  Started: {session.started_at}")
    click.echo(f"  LLM calls: {tokens.llm_call_count}")
    click.echo()

    click.echo(f"  {'Token Breakdown':<30}  {'Tokens':>12}  {'Cost':>10}")
    click.echo(f"  {'─' * 58}")
    click.echo(
        f"  {'Input (uncached)':<30}  {tokens.input_tokens:>12,}  "
        f"${cost_data.input_cost:>8.4f}"
    )
    click.echo(
        f"  {'Cache creation':<30}  {tokens.cache_creation_tokens:>12,}  "
        f"${cost_data.cache_create_cost:>8.4f}"
    )
    click.echo(
        f"  {'Cache reads':<30}  {tokens.cache_read_tokens:>12,}  "
        f"${cost_data.cache_read_cost:>8.4f}"
    )
    click.echo(
        f"  {'Output':<30}  {tokens.output_tokens:>12,}  "
        f"${cost_data.output_cost:>8.4f}"
    )
    total_tokens = (
        tokens.input_tokens + tokens.cache_creation_tokens
        + tokens.cache_read_tokens + tokens.output_tokens
    )
    click.echo(f"  {'─' * 58}")
    click.echo(
        f"  {'Total':<30}  {total_tokens:>12,}  "
        f"${cost_data.total_cost:>8.4f}"
    )

    if ss and ss.tools:
        click.echo()
        click.echo(f"  Tool calls: {ss.total_calls}")
        top = sorted(ss.tools, key=lambda t: t.call_count, reverse=True)[:5]
        tools_str = ", ".join(
            f"{t.tool_name} ({t.call_count})" for t in top
        )
        click.echo(f"  Top tools: {tools_str}")

    click.echo()


def _show_recent_costs(db: MeterDB, limit: int) -> None:
    """Show cost summary for recent sessions."""
    sessions = db.get_sessions(limit=limit)

    if not sessions:
        click.echo("\n  No sessions recorded.\n")
        return

    click.echo()
    click.echo("  Recent Session Costs")
    click.echo(f"  {'─' * 70}")

    any_cost = False
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
        project = session.server_command.rstrip("/").rsplit("/", 1)[-1]
        name = session.name or session.id[:12]
        total_tokens = (
            tokens.input_tokens + tokens.cache_creation_tokens
            + tokens.cache_read_tokens + tokens.output_tokens
        )

        click.echo(
            f"  {name:<35}  {project:<15}  "
            f"{total_tokens:>12,} tok  "
            f"${cost_data.total_cost:>8.2f}"
        )
        any_cost = True

    if not any_cost:
        click.echo("  No session transcripts found.")
        click.echo("  (Cost data requires Claude Code with hook installed)")

    click.echo()
