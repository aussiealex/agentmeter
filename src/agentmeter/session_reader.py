"""Read real token data from agent session transcript files.

Claude Code writes every message to a JSONL file on disk. Each assistant
message includes a usage block with real token counts from the Anthropic
API response. This module reads that data for accurate cost calculation.

See specs/session-token-reader.md for full design.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentmeter.models import RateCard, SessionCost, SessionTokens


def read_session_tokens_from_file(path: Path) -> SessionTokens | None:
    """Read token data from a session JSONL file.

    Returns None if the file doesn't exist.
    Returns SessionTokens with zero counts if the file is empty or
    contains no assistant messages with usage data.
    Skips malformed lines gracefully.
    """
    if not path.exists():
        return None

    tokens = SessionTokens()

    try:
        with open(path) as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                if data.get("type") != "assistant":
                    continue

                message = data.get("message")
                if not isinstance(message, dict):
                    continue

                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue

                tokens.input_tokens += usage.get("input_tokens", 0)
                tokens.cache_creation_tokens += usage.get(
                    "cache_creation_input_tokens", 0,
                )
                tokens.cache_read_tokens += usage.get(
                    "cache_read_input_tokens", 0,
                )
                tokens.output_tokens += usage.get("output_tokens", 0)
                tokens.llm_call_count += 1

                if not tokens.model_id:
                    tokens.model_id = message.get("model", "")

    except Exception as exc:
        print(
            f"agentmeter: error reading session JSONL: {exc}",
            file=sys.stderr,
            flush=True,
        )

    return tokens


def read_session_tokens(
    session_id: str,
    project_dir: str = "",
    claude_dir: Path | None = None,
) -> SessionTokens | None:
    """Read token data for a session by finding and parsing its JSONL file."""
    path = find_session_jsonl(session_id, project_dir, claude_dir)
    if path is None:
        return None

    tokens = read_session_tokens_from_file(path)
    if tokens is not None:
        tokens.session_id = session_id
    return tokens


def calculate_session_cost(
    tokens: SessionTokens, rate: RateCard,
) -> SessionCost:
    """Calculate cost breakdown from real tokens and a rate card."""
    input_cost = tokens.input_tokens * rate.input_per_mtok / 1_000_000
    cache_create_cost = (
        tokens.cache_creation_tokens * rate.input_per_mtok / 1_000_000
    )
    cache_read_cost = (
        tokens.cache_read_tokens * rate.cached_per_mtok / 1_000_000
    )
    output_cost = tokens.output_tokens * rate.output_per_mtok / 1_000_000

    return SessionCost(
        input_cost=input_cost,
        cache_create_cost=cache_create_cost,
        cache_read_cost=cache_read_cost,
        output_cost=output_cost,
        total_cost=input_cost + cache_create_cost + cache_read_cost + output_cost,
    )


def derive_project_slug(project_dir: str) -> str:
    """Derive Claude Code's project slug from a directory path.

    Claude Code converts absolute paths to slugs by replacing / with -:
        /media/aa/LargeBackup/MainApps/AgentMeter
        → -media-aa-LargeBackup-MainApps-AgentMeter
    """
    if not project_dir or project_dir == "/":
        return ""
    return project_dir.replace("/", "-")


def find_session_jsonl(
    session_id: str,
    project_dir: str,
    claude_dir: Path | None = None,
) -> Path | None:
    """Find the JSONL file for a session.

    Looks at: <claude_dir>/projects/<slug>/<session-id>.jsonl
    """
    if claude_dir is None:
        claude_dir = Path.home() / ".claude"

    slug = derive_project_slug(project_dir)
    if not slug:
        return None

    jsonl = claude_dir / "projects" / slug / f"{session_id}.jsonl"
    if jsonl.exists():
        return jsonl

    return None
