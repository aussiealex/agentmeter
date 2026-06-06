"""CLI commands for hook management."""

from __future__ import annotations

import json as _json
import shutil
import sys

import click

from agentmeter.db import MeterDB

AGENTS = ["claude", "gemini", "codex", "copilot"]

# Map agent name → (hook module, hook event name, config file path hint)
_AGENT_CONFIG = {
    "claude": {
        "module": "agentmeter.hooks.claude",
        "event": "PostToolUse",
        "config_file": "~/.claude/settings.json",
        "format": "json",
        "server_name": "claude-code",
    },
    "gemini": {
        "module": "agentmeter.hooks.gemini",
        "event": "AfterTool",
        "config_file": "~/.gemini/settings.json",
        "format": "json",
        "server_name": "gemini-cli",
    },
    "codex": {
        "module": "agentmeter.hooks.codex",
        "event": "PostToolUse",
        "config_file": "~/.codex/config.toml",
        "format": "toml",
        "server_name": "codex-cli",
    },
    "copilot": {
        "module": "agentmeter.hooks.copilot",
        "event": "postToolUse",
        "config_file": ".github/hooks/agentmeter.json",
        "format": "copilot-json",
        "server_name": "copilot-cli",
    },
}


@click.group()
def hook() -> None:
    """Manage agent hooks for tool call metering."""


@hook.command("install")
@click.argument("agent", type=click.Choice(AGENTS), default="claude")
@click.option(
    "--coach", "include_coach", is_flag=True, default=False,
    help="Include PreToolUse coach hook for yellow card coaching.",
)
def hook_install(agent: str, include_coach: bool) -> None:
    """Print hook configuration for an agent.

    Generates the config snippet to add to the agent's settings file.
    Use --coach to include the yellow card PreToolUse hook.

    Examples:
        agentmeter hook install claude
        agentmeter hook install claude --coach
        agentmeter hook install gemini
    """
    cfg = _AGENT_CONFIG[agent]
    python_path = shutil.which("python3") or shutil.which("python") or sys.executable
    command = f"{python_path} -m {cfg['module']}"
    coach_command = f"{python_path} -m agentmeter.hooks.coach"

    if cfg["format"] == "json":
        coach = coach_command if include_coach else None
        _print_json_config(agent, cfg, command, coach)
    elif cfg["format"] == "toml":
        _print_toml_config(agent, cfg, command)
        if include_coach:
            click.echo(
                "\n# Coach hook (PreToolUse)"
                " — not yet supported for TOML agents.",
            )
    elif cfg["format"] == "copilot-json":
        _print_copilot_config(agent, cfg, command)
        if include_coach:
            click.echo("\n# Coach hook — not yet supported for Copilot.")


def _print_json_config(
    agent: str, cfg: dict, command: str,
    coach_command: str | None = None,
) -> None:
    """Print JSON hook config for Claude Code or Gemini CLI."""
    hooks = {
        cfg["event"]: [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 2000,
                    },
                ],
            },
        ],
    }

    if coach_command:
        hooks["PreToolUse"] = [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": coach_command,
                        "timeout": 2000,
                    },
                ],
            },
        ]

    config = {"hooks": hooks}
    click.echo(f"Add this to {cfg['config_file']}:\n")
    click.echo(_json.dumps(config, indent=2))
    events = cfg["event"]
    if coach_command:
        events += " and PreToolUse"
    click.echo(
        f"\nOr merge the {events} array(s) into your existing hooks config.",
    )


def _print_toml_config(agent: str, cfg: dict, command: str) -> None:
    """Print TOML hook config for Codex CLI."""
    click.echo(f"Add this to {cfg['config_file']}:\n")
    click.echo(f'[[hooks.{cfg["event"]}]]')
    click.echo('matcher = "*"')
    click.echo(f'command = "{command}"')
    click.echo("timeout = 2000")
    click.echo(
        f'\nOr add to an existing [[hooks.{cfg["event"]}]] section.'
    )


def _print_copilot_config(agent: str, cfg: dict, command: str) -> None:
    """Print JSON hook config for GitHub Copilot CLI."""
    config = {
        "version": 1,
        "hooks": [
            {
                "event": cfg["event"],
                "command": command,
                "timeout": 2000,
            },
        ],
    }
    click.echo(f"Save this as {cfg['config_file']}:\n")
    click.echo(_json.dumps(config, indent=2))
    click.echo(
        "\nThe .github/hooks/ directory is auto-discovered by Copilot CLI."
    )


@hook.command("status")
@click.option(
    "--agent", "-a", default=None, type=click.Choice(AGENTS),
    help="Filter by agent (default: all).",
)
def hook_status(agent: str | None) -> None:
    """Show hook metering stats."""
    db = MeterDB()

    if agent:
        server_name = _AGENT_CONFIG[agent]["server_name"]
        _print_agent_stats(db, agent, server_name)
    else:
        any_data = False
        for a, cfg in _AGENT_CONFIG.items():
            stats = db.get_tool_stats(server_name=cfg["server_name"])
            if stats:
                any_data = True
                _print_agent_stats(db, a, cfg["server_name"])
        if not any_data:
            click.echo("No hook data yet. Run: agentmeter hook install <agent>")

    db.close()


def _print_agent_stats(db: MeterDB, agent: str, server_name: str) -> None:
    stats = db.get_tool_stats(server_name=server_name)
    if not stats:
        click.echo(f"\n  {agent}: no data yet")
        return

    total = sum(s.call_count for s in stats)
    click.echo(f"\n  {agent}: {total} tool calls across {len(stats)} tools")
    for s in sorted(stats, key=lambda x: x.call_count, reverse=True):
        click.echo(f"    {s.tool_name:<20} {s.call_count:>5} calls")
