"""Export command — JSONL export of tool call data."""

from __future__ import annotations

import json

import click

from agentmeter.db import MeterDB


@click.command()
@click.option(
    "--since", "-s", default=None,
    help="Only calls after this date (YYYY-MM-DD).",
)
@click.option("--tool", "-t", default=None, help="Filter by tool name.")
@click.option("--session", default=None, help="Filter by session ID.")
@click.option(
    "--limit", "-l", default=None, type=int,
    help="Maximum number of calls to export.",
)
def export(
    since: str | None,
    tool: str | None,
    session: str | None,
    limit: int | None,
) -> None:
    """Export tool call data as JSONL (one JSON object per line).

    Output goes to stdout so it can be piped or redirected.

    Examples:
        agentmeter export > calls.jsonl
        agentmeter export --since 2026-05-01
        agentmeter export --tool Read --limit 100
        agentmeter export --session sess-abc123
    """
    db = MeterDB()
    calls = db.get_calls_for_export(
        since=since,
        tool_name=tool,
        session_id=session,
        limit=limit,
    )
    db.close()

    for c in calls:
        record = {
            "id": c.id,
            "session_id": c.session_id,
            "server_name": c.server_name,
            "tool_name": c.tool_name,
            "arguments_json": c.arguments_json,
            "result_size": c.result_size,
            "is_error": c.is_error,
            "started_at": c.started_at,
            "elapsed_ms": c.elapsed_ms,
            "created_at": c.created_at,
            "agent": c.agent,
            "project": c.project,
            "model_id": c.model_id,
            "input_size": c.input_size,
        }
        click.echo(json.dumps(record, separators=(",", ":")))
