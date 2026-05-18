"""CLI commands for hook management."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB


@click.group()
def hook() -> None:
    """Manage the PostToolUse hook for Claude Code built-in tools."""


@hook.command("install")
def hook_install() -> None:
    """Print the hook configuration to add to Claude Code settings.

    Paste the output into ~/.claude/settings.json under the "hooks" key.
    """
    import json as _json
    import shutil

    python_path = shutil.which("python3") or "python3"

    config = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{python_path} -m agentmeter.hook",
                            "timeout": 2000,
                        },
                    ],
                },
            ],
        },
    }

    click.echo("Add this to ~/.claude/settings.json:\n")
    click.echo(_json.dumps(config, indent=2))
    click.echo(
        "\nOr merge the PostToolUse array into your existing hooks config."
    )


@hook.command("status")
def hook_status() -> None:
    """Show hook metering stats (claude-code sessions)."""
    db = MeterDB()
    stats = db.get_tool_stats(server_name="claude-code")
    if not stats:
        click.echo("No hook data yet. Is the PostToolUse hook installed?")
        db.close()
        return

    total = sum(s.call_count for s in stats)
    click.echo(f"Hook metering: {total} tool calls across {len(stats)} tools\n")
    for s in sorted(stats, key=lambda x: x.call_count, reverse=True):
        click.echo(f"  {s.tool_name:<20} {s.call_count:>5} calls")
    db.close()
