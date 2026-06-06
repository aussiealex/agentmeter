"""CLI commands for session coaching — review, config, and profiling."""

from __future__ import annotations

import json
from pathlib import Path

import click

from agentmeter.db import MeterDB
from agentmeter.heuristics import AnalysisContext, Finding, analyse_session
from agentmeter.platform import data_dir, project_name
from agentmeter.session_reader import (
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)

_DEFAULT_CONFIG = {
    "enabled": True,
    "threshold_calls": [50, 100],
    "threshold_repeat": 15,
}


def _coach_dir() -> Path:
    return data_dir() / "coach"


def _config_path() -> Path:
    return _coach_dir() / "config.json"


def _read_config() -> dict:
    path = _config_path()
    if not path.exists():
        return dict(_DEFAULT_CONFIG)
    try:
        cfg = json.loads(path.read_text())
        # Merge with defaults for missing keys
        merged = dict(_DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT_CONFIG)


def _write_config(config: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")


@click.group()
def coach() -> None:
    """Session coaching — efficiency analysis and advice."""


@coach.command()
@click.argument("session_id", required=False)
@click.option(
    "--project", "-p", default=None,
    help="Filter to the most recent session for a project.",
)
def review(session_id: str | None, project: str | None) -> None:
    """Review a session for efficiency patterns.

    Analyses tool call patterns from a single session and gives
    actionable advice on how to reduce cost next time.

    If SESSION_ID is omitted, reviews the most recent session.
    Use -p to filter by project name (substring match).
    Partial IDs work (e.g. first 8 characters).

    Examples:
        agentmeter coach review
        agentmeter coach review -p PolicyGuardian
        agentmeter coach review 4d44d158
    """
    db = MeterDB()

    session = _resolve_session(db, session_id, project)
    if not session:
        label = session_id or "any"
        click.echo(f"\n  No session found: {label}\n")
        db.close()
        return

    # Session header
    project = project_name(session.server_command)
    display = session.name or project or session.id[:8]
    started = session.started_at[:16].replace("T", " ") if session.started_at else ""

    click.echo()
    click.echo(f"  Session Review — {display}")
    click.echo(f"  {started}")
    click.echo(f"  {'─' * 55}")

    # Get tool call stats for this session
    stats = _session_tool_stats(db, session.id)
    total_calls = sum(stats.values())

    if total_calls == 0:
        click.echo("  No tool calls recorded for this session.")
        click.echo()
        db.close()
        return

    # Cost data
    cost_str = ""
    tokens = None
    rate = None
    jsonl_path = find_session_jsonl(session.id, session.server_command)
    if jsonl_path:
        tokens = read_session_tokens_from_file(jsonl_path)
        if tokens and tokens.llm_call_count > 0:
            rate = db.get_rate(tokens.model_id)
            if rate:
                cost_data = calculate_session_cost(tokens, rate)
                cost_str = f"  ${cost_data.total_cost:.2f}"

    # Tool breakdown
    click.echo(f"  {total_calls} tool calls{cost_str}")
    click.echo()
    for tool, count in sorted(stats.items(), key=lambda x: -x[1]):
        pct = count / total_calls * 100
        bar_len = int(pct / 100 * 20)
        bar = "█" * bar_len
        click.echo(f"    {tool:<20}  {bar}  {count:>4} ({pct:.0f}%)")

    # Outcome
    if session.outcome:
        click.echo()
        parts = []
        if session.commits:
            parts.append(f"{session.commits} commits")
        if session.files_changed:
            parts.append(f"{session.files_changed} files changed")
        if session.tests_passed:
            parts.append(f"{session.tests_passed} tests passed")
        if session.tests_failed:
            parts.append(f"{session.tests_failed} tests failed")
        click.echo(f"  Outcome: {', '.join(parts) if parts else session.outcome}")

    # Run heuristics
    ctx = AnalysisContext(
        conn=db._conn, session_id=session.id,
        tokens=tokens, rate=rate,
    )
    findings = analyse_session(ctx)

    # Score
    score = _efficiency_score(findings, total_calls)
    click.echo()
    click.echo(f"  Efficiency: {score}/10")

    # Findings
    if findings:
        _print_review_findings(findings)
    else:
        click.echo()
        click.echo("  No patterns detected — clean session.")

    click.echo()
    db.close()


# -------------------------------------------------------------------
# coach start — session-start profiling
# -------------------------------------------------------------------

@coach.command("start")
@click.option(
    "--type", "-t", "task_type",
    type=click.Choice(["development", "personal", "research"]),
    default=None,
    help="Task type (affects thresholds).",
)
def start(task_type: str | None) -> None:
    """Session-start profiling with tailored guidance.

    Detects project maturity, pulls historical cost data, and
    outputs coaching guidance for the current session. Also adjusts
    yellow card thresholds based on context.

    \b
    Examples:
        agentmeter coach start
        agentmeter coach start --type development
        agentmeter coach start --type personal
    """
    import os

    cwd = os.environ.get("PWD", os.getcwd())
    project = project_name(cwd)

    # Detect project maturity
    maturity, maturity_signals = _detect_maturity(cwd)

    # Get historical data
    db = MeterDB()
    history = _project_history(db, project)
    db.close()

    # Determine task type if not specified
    if not task_type:
        task_type = "development"

    # Set thresholds based on context
    call_thresholds, repeat_threshold = _contextual_thresholds(
        task_type, maturity,
    )

    # Update config with contextual thresholds
    config = _read_config()
    config["threshold_calls"] = call_thresholds
    config["threshold_repeat"] = repeat_threshold
    _write_config(config)

    # Output guidance
    maturity_label = _maturity_label(maturity)
    click.echo("AgentMeter Coach — Session Context")
    click.echo()
    click.echo(f"Project: {project} (maturity: {maturity}/4 — {maturity_label})")
    click.echo(f"Task type: {task_type}")

    if history["sessions"] > 0:
        click.echo(
            f"History: avg {history['avg_calls']:.0f} calls/session, "
            f"${history['avg_cost']:.2f}/session, "
            f"{history['commit_rate']:.0f}% commit rate",
        )

    click.echo()
    _print_guidance(task_type, maturity, maturity_signals, history)

    click.echo()
    click.echo(
        f"Yellow card thresholds: "
        f"{', '.join(str(t) for t in call_thresholds)} calls | "
        f"{repeat_threshold}x same tool",
    )


# -------------------------------------------------------------------
# coach context — CLAUDE.md block generation
# -------------------------------------------------------------------

@coach.command("context")
@click.option("--project", "-p", default=None, help="Project name override.")
def context(project: str | None) -> None:
    """Generate a CLAUDE.md coaching context block.

    Outputs a block suitable for injection into CLAUDE.md or
    session-start hooks. Examines the current project and
    recent cost history.

    \b
    Examples:
        agentmeter coach context
        agentmeter coach context >> CLAUDE.md
    """
    import os

    cwd = os.environ.get("PWD", os.getcwd())
    proj = project or project_name(cwd)

    maturity, _ = _detect_maturity(cwd)
    db = MeterDB()
    history = _project_history(db, proj)
    db.close()

    maturity_label = _maturity_label(maturity)

    click.echo("## AgentMeter Session Context")
    click.echo()
    click.echo(
        f"Project maturity: {maturity_label} "
        f"({maturity}/4)",
    )

    if history["sessions"] > 0:
        click.echo(
            f"Recent avg: {history['avg_calls']:.0f} calls/session, "
            f"${history['avg_cost']:.2f}/session, "
            f"{history['commit_rate']:.0f}% commit rate",
        )

    click.echo()

    if maturity >= 3:
        click.echo("Prompting guidance:")
        click.echo("- Reference specs and existing code by path and line number.")
        click.echo(
            "- Batch related changes into single prompts"
            " with acceptance criteria.",
        )
        click.echo("- Avoid exploratory reads — state which files you need and why.")
        if history["avg_calls"] > 0:
            target = max(int(history["avg_calls"] * 0.7), 15)
            click.echo(f"- Target: <{target} calls for a focused feature.")
    elif maturity >= 1:
        click.echo("Prompting guidance:")
        click.echo("- Project has some structure. Use existing docs before exploring.")
        click.echo("- Write acceptance criteria before coding.")
        click.echo("- If exploring, summarise findings before acting.")
    else:
        click.echo("Prompting guidance:")
        click.echo("- Greenfield project — exploration is expected.")
        click.echo("- Once you find what you need, summarise before acting.")
        click.echo(
            "- If session exceeds 100 calls, consider a"
            " fresh start with a brief.",
        )


# -------------------------------------------------------------------
# coach show / set / on / off — config management
# -------------------------------------------------------------------

@coach.command("show")
def show() -> None:
    """Show current coaching configuration."""
    config = _read_config()
    enabled = config.get("enabled", True)
    calls = config.get("threshold_calls", [50, 100])
    repeat = config.get("threshold_repeat", 15)

    click.echo()
    click.echo(f"  Coaching: {'enabled' if enabled else 'disabled'}")
    click.echo(f"  Call thresholds: {', '.join(str(c) for c in calls)}")
    click.echo(f"  Repeat threshold: {repeat}x same tool")

    # Show active sessions
    coach_dir = _coach_dir()
    if coach_dir.exists():
        state_files = list(coach_dir.glob("*.json"))
        # Exclude config.json
        sessions = [
            f for f in state_files if f.name != "config.json"
        ]
        if sessions:
            click.echo(f"  Active state files: {len(sessions)}")

    click.echo()


@coach.command("set")
@click.argument("key", type=click.Choice(["calls", "repeat"]))
@click.argument("values", nargs=-1, type=int, required=True)
def set_threshold(key: str, values: tuple[int, ...]) -> None:
    """Set coaching thresholds.

    \b
    Examples:
        agentmeter coach set calls 50          # warn at 50 calls
        agentmeter coach set calls 50 100      # warn at 50 and 100
        agentmeter coach set repeat 15         # same tool 15 times
    """
    if not values:
        click.echo("Provide at least one value.")
        return

    config = _read_config()

    if key == "calls":
        config["threshold_calls"] = sorted(values)
        click.echo(
            f"  Call thresholds set to: "
            f"{', '.join(str(v) for v in sorted(values))}",
        )
    elif key == "repeat":
        config["threshold_repeat"] = values[0]
        click.echo(f"  Repeat threshold set to: {values[0]}")

    _write_config(config)


@coach.command("on")
def on() -> None:
    """Enable coaching (yellow cards will fire)."""
    config = _read_config()
    config["enabled"] = True
    _write_config(config)
    click.echo("  Coaching enabled.")


@coach.command("off")
def off() -> None:
    """Disable coaching (yellow cards will not fire)."""
    config = _read_config()
    config["enabled"] = False
    _write_config(config)
    click.echo("  Coaching disabled.")


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _resolve_session(
    db: MeterDB, session_id: str | None,
    project: str | None = None,
):
    """Find session by partial ID, name, project, or most recent."""
    sessions = db.get_sessions(limit=100)
    if not sessions:
        return None

    if session_id:
        for s in sessions:
            if (s.id == session_id
                    or s.id.startswith(session_id)
                    or (s.name and s.name == session_id)):
                return s
        return None

    if project:
        query = project.lower()
        for s in sessions:
            proj = project_name(s.server_command).lower()
            if query in proj or proj in query:
                return s
        return None

    return sessions[0]  # most recent


def _session_tool_stats(db: MeterDB, session_id: str) -> dict[str, int]:
    """Get {tool_name: count} for a specific session."""
    rows = db._conn.execute(
        "SELECT tool_name, COUNT(*) as cnt "
        "FROM tool_call WHERE session_id = ? "
        "GROUP BY tool_name ORDER BY cnt DESC",
        (session_id,),
    ).fetchall()
    return {r["tool_name"]: r["cnt"] for r in rows}


def _efficiency_score(findings: list[Finding], total_calls: int) -> int:
    """Score from 1-10 based on findings severity and count."""
    if not findings:
        return 10

    penalty = 0
    for f in findings:
        if f.severity == "critical":
            penalty += 3
        elif f.severity == "warning":
            penalty += 1.5
        else:
            penalty += 0.5

    # Scale penalty — more calls means more tolerance
    if total_calls > 100:
        penalty *= 0.8
    elif total_calls < 30:
        penalty *= 1.2

    score = max(1, min(10, round(10 - penalty)))
    return score


def _print_review_findings(findings: list[Finding]) -> None:
    """Print findings grouped by severity."""
    click.echo()
    click.echo("  Patterns detected:")

    for f in findings:
        if f.severity == "critical":
            marker = "!!"
        elif f.severity == "warning":
            marker = " !"
        else:
            marker = "  "

        click.echo(f"  {marker} {f.summary}")
        click.echo(f"  {' ' * len(marker)} {f.advice}")


def _detect_maturity(cwd: str) -> tuple[int, list[str]]:
    """Score project maturity 0-4 from filesystem signals."""
    root = Path(cwd)
    score = 0
    signals = []

    if (root / "CLAUDE.md").exists():
        score += 1
        signals.append("CLAUDE.md")

    specs = (
        (root / "specs").is_dir()
        or (root / ".specify").is_dir()
        or (root / "docs").is_dir()
    )
    if specs:
        score += 1
        signals.append("specs/docs")

    if (root / "tests").is_dir():
        test_files = list((root / "tests").glob("test_*.py"))
        if test_files:
            score += 1
            signals.append(f"tests ({len(test_files)} files)")

    ci = (
        (root / ".github" / "workflows").is_dir()
        or (root / ".gitlab-ci.yml").exists()
        or (root / "Makefile").exists()
    )
    if ci:
        score += 1
        signals.append("CI/build")

    return score, signals


def _project_history(db: MeterDB, project: str) -> dict:
    """Pull recent stats for a project."""
    from datetime import datetime, timedelta

    since = (datetime.now() - timedelta(days=30)).isoformat()
    sessions = db.get_sessions(limit=200)

    matching = [
        s for s in sessions
        if project_name(s.server_command) == project
        and s.started_at >= since
    ]

    if not matching:
        return {
            "sessions": 0, "avg_calls": 0,
            "avg_cost": 0, "commit_rate": 0,
        }

    total_cost = 0.0
    cost_sessions = 0
    for s in matching:
        jsonl_path = find_session_jsonl(s.id, s.server_command)
        if jsonl_path:
            tokens = read_session_tokens_from_file(jsonl_path)
            if tokens and tokens.llm_call_count > 0:
                rate = db.get_rate(tokens.model_id)
                if rate:
                    cost_data = calculate_session_cost(tokens, rate)
                    total_cost += cost_data.total_cost
                    cost_sessions += 1

    # Avg calls from DB
    total_calls = sum(s.total_calls for s in matching)
    committed = sum(1 for s in matching if s.commits > 0)

    n = len(matching)
    return {
        "sessions": n,
        "avg_calls": total_calls / n if n else 0,
        "avg_cost": total_cost / cost_sessions if cost_sessions else 0,
        "commit_rate": committed / n * 100 if n else 0,
    }


def _contextual_thresholds(
    task_type: str, maturity: int,
) -> tuple[list[int], int]:
    """Return (call_thresholds, repeat_threshold) for context."""
    if task_type == "research":
        return [100, 200], 25

    if task_type == "personal":
        return [80, 150], 20

    # Development — tighter for mature projects
    if maturity >= 3:
        return [40, 80], 12
    if maturity >= 2:
        return [50, 100], 15
    return [60, 120], 18


def _maturity_label(maturity: int) -> str:
    labels = {
        0: "greenfield", 1: "early", 2: "established",
        3: "mature", 4: "fully documented",
    }
    return labels.get(maturity, "unknown")


def _print_guidance(
    task_type: str, maturity: int,
    signals: list[str], history: dict,
) -> None:
    """Print contextual prompting guidance."""
    signal_str = ", ".join(signals) if signals else "none detected"
    click.echo(f"Signals: {signal_str}")
    click.echo()

    if maturity >= 3 and task_type == "development":
        click.echo("This project is well-documented. The agent should NOT")
        click.echo("need to explore — everything is documented.")
        click.echo()
        click.echo("Prompting strategy:")
        click.echo("  - Reference exact files by path and line number")
        click.echo("  - State acceptance criteria upfront")
        click.echo("  - Batch related changes: \"do A, B, and C in one pass\"")
        click.echo("  - If you catch yourself saying \"look at...\" — be specific")
    elif maturity <= 1 and task_type == "personal":
        click.echo("Exploration territory. Higher call counts expected —")
        click.echo("you're discovering the shape of the problem.")
        click.echo()
        click.echo("Prompting strategy:")
        click.echo("  - Explore freely but notice when going in circles")
        click.echo("  - Once you find what you need, summarise before acting")
        click.echo("  - If session exceeds 100 calls, write a brief and start fresh")
    elif task_type == "research":
        click.echo("Research session — reading and understanding is the goal.")
        click.echo("Relaxed thresholds apply.")
        click.echo()
        click.echo("Prompting strategy:")
        click.echo("  - Read freely — understanding is the output")
        click.echo("  - Summarise findings periodically")
        click.echo("  - Consider writing notes as you go")
    else:
        click.echo("Prompting strategy:")
        click.echo("  - Be specific about what you want changed and where")
        click.echo("  - State what 'done' looks like before starting")
        click.echo("  - Use existing docs and specs as references")
        if history["avg_calls"] > 0:
            target = max(int(history["avg_calls"] * 0.8), 20)
            click.echo(f"  - Target: <{target} calls for this session")
