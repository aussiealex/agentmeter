"""Formatting helpers for AgentMeter CLI output."""

from __future__ import annotations

import click

from agentmeter.db import MeterDB


def print_distribution(db: MeterDB, server: str | None) -> None:
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
            f"  {'tool time':>18}  {format_ms(d.p50_elapsed_ms):>10}  "
            f"{format_ms(d.p90_elapsed_ms):>10}  {format_ms(d.p99_elapsed_ms):>10}"
        )
        rb = (d.p50_result_bytes, d.p90_result_bytes, d.p99_result_bytes)
        click.echo(
            f"  {'result size':>18}  {format_bytes(rb[0]):>10}  "
            f"{format_bytes(rb[1]):>10}  {format_bytes(rb[2]):>10}"
        )

    click.echo()


def print_tool_table(tools: list, total_calls: int) -> None:
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


def format_ms(ms: int) -> str:
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


def format_bytes(size: int) -> str:
    """Format byte count into human-readable size."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"
