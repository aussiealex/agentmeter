"""PreToolUse coach hook — yellow card mid-session coaching.

Reads the coach state file written by the PostToolUse hook and checks
thresholds. When a threshold is crossed, blocks ONE tool call with a
coaching message (exit 1). The agent sees the message, retries, and
the retry passes through.

Performance budget: <2ms. No DB queries. Reads two small JSON files.

Entry point: python3 -m agentmeter.hooks.coach
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

_DEFAULT_CALL_THRESHOLDS = [50, 100]
_DEFAULT_REPEAT_THRESHOLD = 15


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session_id = data.get("session_id", "")
    if not session_id:
        return

    with contextlib.suppress(Exception):
        _check(session_id)


def _check(session_id: str) -> None:
    coach_dir = _coach_dir()

    # Check global enable/disable
    config = _read_json(coach_dir / "config.json")
    if not config.get("enabled", True):
        return

    # Read session state
    state_path = coach_dir / f"{session_id}.json"
    state = _read_json(state_path)
    if not state:
        return

    calls = state.get("calls", 0)
    tools = state.get("tools", {})
    warnings_fired = set(state.get("warnings_fired", []))

    # Thresholds: session-specific > config > defaults
    call_levels = config.get(
        "threshold_calls", _DEFAULT_CALL_THRESHOLDS,
    )
    repeat_limit = config.get(
        "threshold_repeat", _DEFAULT_REPEAT_THRESHOLD,
    )

    message = None
    warning_key = None

    # Check call count thresholds
    for level in sorted(call_levels):
        key = f"calls_{level}"
        if calls >= level and key not in warnings_fired:
            message = _call_count_message(calls, tools, level)
            warning_key = key
            break

    # Check repeated tool
    if not message:
        for tool_name, count in sorted(
            tools.items(), key=lambda x: -x[1],
        ):
            key = f"repeat_{tool_name}"
            if count >= repeat_limit and key not in warnings_fired:
                message = _repeated_tool_message(
                    tool_name, count, calls,
                )
                warning_key = key
                break

    if not message:
        return

    # Record warning so it doesn't fire again for this threshold
    warnings_fired.add(warning_key)
    state["warnings_fired"] = list(warnings_fired)
    with contextlib.suppress(OSError):
        state_path.write_text(json.dumps(state))

    # Block the tool call — agent sees this message
    print(message)
    sys.exit(1)


def _coach_dir() -> Path:
    from agentmeter.platform import data_dir

    return data_dir() / "coach"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _call_count_message(
    calls: int, tools: dict, threshold: int,
) -> str:
    top = sorted(tools.items(), key=lambda x: -x[1])[:3]
    tool_line = ", ".join(f"{t} {c}x" for t, c in top)

    return (
        f"AgentMeter — cost checkpoint\n"
        f"\n"
        f"Session: {calls} tool calls\n"
        f"Top tools: {tool_line}\n"
        f"\n"
        f"Your session has passed {threshold} tool calls. Each turn\n"
        f"replays your full conversation as input tokens — costs grow\n"
        f"quadratically, not linearly.\n"
        f"\n"
        f"Consider:\n"
        f"  - Act on what you have rather than exploring more\n"
        f"  - Start a fresh session with a focused brief for remaining work\n"
        f"  - A fresh session resets the context accumulator to zero\n"
        f"\n"
        f"This is informational. Retry your action to continue."
    )


def _repeated_tool_message(
    tool_name: str, count: int, total: int,
) -> str:
    pct = count / total * 100 if total > 0 else 0
    advice = _tool_advice(tool_name)
    return (
        f"AgentMeter — cost checkpoint\n"
        f"\n"
        f"Session: {total} tool calls\n"
        f"Pattern: {tool_name} called {count} times"
        f" ({pct:.0f}% of session)\n"
        f"\n"
        f"{advice}\n"
        f"\n"
        f"This is informational. Retry your action to continue."
    )


def _tool_advice(tool_name: str) -> str:
    table = {
        "Read": (
            "High-frequency reads. Try:\n"
            "  - Reference exact files and line ranges in your prompt\n"
            "  - Use Grep to find what you need first\n"
            "  - Tell the agent which files matter and why"
        ),
        "Grep": (
            "Heavy searching. Try:\n"
            "  - Tell the agent what you're looking for and where\n"
            "  - Provide file paths and function names in your prompt"
        ),
        "Glob": (
            "Heavy searching. Try:\n"
            "  - Tell the agent what you're looking for and where\n"
            "  - Provide file paths and function names in your prompt"
        ),
        "Edit": (
            "Many edits. Try:\n"
            "  - Write the full solution in one prompt with clear criteria\n"
            "  - Avoid trial-and-error — state what 'done' looks like"
        ),
        "Bash": (
            "Many shell commands. Try:\n"
            "  - Describe the full problem and expected outcome\n"
            "  - Avoid edit-test loops — write tests first"
        ),
        "TodoWrite": (
            "Task tracking is consuming calls. Try:\n"
            "  - Define steps in your prompt instead\n"
            "  - Suppress TodoWrite if your prompt already has structure"
        ),
    }
    return table.get(tool_name, (
        f"{tool_name} used heavily. A more specific prompt\n"
        f"  could reduce the number of calls needed."
    ))


if __name__ == "__main__":
    main()
