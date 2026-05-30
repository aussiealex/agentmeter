"""Heuristic detection of wasteful tool call patterns.

Analyses the tool_call table to find actionable inefficiencies.
Consumed by advise, coach review, coach stats, and yellow card hook.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from agentmeter.db._helpers import build_where
from agentmeter.models import RateCard, SessionTokens


@dataclass
class AnalysisContext:
    """Input to every heuristic. Grows as data sources mature."""

    conn: sqlite3.Connection
    since: str | None = None
    project: str | None = None
    session_id: str | None = None
    tokens: SessionTokens | None = None
    rate: RateCard | None = None


@dataclass
class Finding:
    """A single detected pattern with actionable advice."""

    pattern: str       # machine-readable: "repeated_file_reads"
    severity: str      # "info", "warning", "critical"
    scope: str         # "session" or "cross-session"
    summary: str       # "constitution.md read 20x across 18 sessions"
    advice: str        # "Inline key content into CLAUDE.md"
    data: dict = field(default_factory=dict)


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse(ctx: AnalysisContext) -> list[Finding]:
    """Run all heuristics, return findings sorted by severity."""
    findings = analyse_session(ctx) + analyse_cross_session(ctx)
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 9))
    return findings


def analyse_session(ctx: AnalysisContext) -> list[Finding]:
    """Run session-scope heuristics only."""
    runners = [
        _many_small_reads,
        _repeated_grep_glob,
        _high_velocity,
        _edit_test_loop,
        _broad_exploration,
        _high_todowrite,
        _low_search_high_read,
        _exploration_no_output,
        _large_result_read,
        _cache_write_waste,
        _low_cache_efficiency,
    ]
    return _run(runners, ctx)


def analyse_cross_session(ctx: AnalysisContext) -> list[Finding]:
    """Run cross-session heuristics only."""
    runners = [
        _repeated_file_cross_session,
        _binary_image_reads,
        _large_result_read,
        _session_size_outlier,
        _project_concentration,
    ]
    return _run(runners, ctx)


def _run(
    runners: list, ctx: AnalysisContext,
) -> list[Finding]:
    findings: list[Finding] = []
    for fn in runners:
        result = fn(ctx)
        if result:
            if isinstance(result, list):
                findings.extend(result)
            else:
                findings.append(result)
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 9))
    return findings


# ---------------------------------------------------------------------------
# Shared query helpers
# ---------------------------------------------------------------------------

def _format_session_time(iso_str: str) -> str:
    """Format ISO timestamp to 'May 22 at 17:33'."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d at %H:%M")
    except (ValueError, TypeError):
        return iso_str[:16]


def _format_duration(start_iso: str, end_iso: str) -> str:
    """Format duration between two ISO timestamps."""
    from datetime import datetime

    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        seconds = int((end - start).total_seconds())
    except (ValueError, TypeError):
        return "unknown"

    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    # If over 2 hours, likely spans overnight — show end time
    if hours >= 2:
        end_str = end.strftime("%b %d at %H:%M")
        return f"– {end_str}"
    if mins:
        return f"{hours}h {mins}m"
    return f"{hours}h"


def _session_clauses(ctx: AnalysisContext) -> tuple[list[str], list]:
    """Build WHERE clauses from context filters."""
    clauses: list[str] = []
    params: list = []
    if ctx.since:
        clauses.append("created_at >= ?")
        params.append(ctx.since)
    if ctx.project:
        clauses.append("project = ?")
        params.append(ctx.project)
    if ctx.session_id:
        clauses.append("session_id = ?")
        params.append(ctx.session_id)
    return clauses, params


def _tool_counts(ctx: AnalysisContext) -> dict[str, int]:
    """Get {tool_name: count} for the filtered scope."""
    clauses, params = _session_clauses(ctx)
    where = build_where(clauses)
    rows = ctx.conn.execute(
        "SELECT tool_name, COUNT(*) as cnt "
        "FROM tool_call " + where + " "
        "GROUP BY tool_name",
        params,
    ).fetchall()
    return {r["tool_name"]: r["cnt"] for r in rows}


def _total_calls(ctx: AnalysisContext) -> int:
    clauses, params = _session_clauses(ctx)
    where = build_where(clauses)
    row = ctx.conn.execute(
        "SELECT COUNT(*) as cnt FROM tool_call " + where, params,
    ).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Session-scope heuristics
# ---------------------------------------------------------------------------

def _many_small_reads(ctx: AnalysisContext) -> Finding | None:
    """Read >15x with avg result_size < 2KB."""
    clauses, params = _session_clauses(ctx)
    clauses.append("tool_name = 'Read'")
    where = build_where(clauses)

    row = ctx.conn.execute(
        "SELECT COUNT(*) as cnt, AVG(result_size) as avg_size "
        "FROM tool_call " + where,
        params,
    ).fetchone()

    if not row or row["cnt"] <= 15:
        return None
    avg_size = row["avg_size"] or 0
    if avg_size >= 2048:
        return None

    return Finding(
        pattern="many_small_reads",
        severity="warning",
        scope="session",
        summary=f"Read called {row['cnt']}x with avg result {avg_size:.0f} bytes",
        advice=(
            "Reference exact files and line ranges in your prompt "
            "instead of letting the agent explore."
        ),
        data={"read_count": row["cnt"], "avg_result_bytes": round(avg_size)},
    )


def _repeated_grep_glob(ctx: AnalysisContext) -> Finding | None:
    """Grep or Glob >10x in scope."""
    counts = _tool_counts(ctx)
    grep = counts.get("Grep", 0)
    glob = counts.get("Glob", 0)
    total = grep + glob

    if total <= 10:
        return None

    return Finding(
        pattern="repeated_grep_glob",
        severity="warning",
        scope="session",
        summary=f"Grep+Glob called {total}x (Grep {grep}, Glob {glob})",
        advice=(
            "Tell the agent what you're looking for and where — "
            "don't let it search."
        ),
        data={"grep_count": grep, "glob_count": glob},
    )


def _high_velocity(ctx: AnalysisContext) -> Finding | None:
    """>10 calls/min sustained for 3+ minutes."""
    clauses, params = _session_clauses(ctx)
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT created_at FROM tool_call " + where + " "
        "ORDER BY created_at",
        params,
    ).fetchall()

    if len(rows) < 30:
        return None

    # Check sliding 3-minute windows
    timestamps = [r["created_at"] for r in rows]
    window_seconds = 180
    max_rate = 0.0
    for i, start_ts in enumerate(timestamps):
        # Find calls within 3 minutes of this one
        count = 0
        for j in range(i, len(timestamps)):
            if timestamps[j] <= start_ts[:19].replace("T", " "):
                count += 1
            # Rough check: compare ISO strings (works for same-day)
            else:
                count += 1
        # Simpler approach: count calls in each 3-min bucket
        break  # Use bucket approach below

    # Bucket approach: count calls per minute
    if not timestamps:
        return None

    from datetime import datetime

    parsed = []
    for ts in timestamps:
        try:
            parsed.append(datetime.fromisoformat(ts))
        except ValueError:
            continue

    if len(parsed) < 30:
        return None

    # Find any 3-minute window with >30 calls (>10/min)
    sustained = False
    for i in range(len(parsed)):
        window_end = parsed[i].timestamp() + window_seconds
        count = sum(
            1 for p in parsed[i:]
            if p.timestamp() <= window_end
        )
        rate = count / 3.0  # calls per minute
        if rate > max_rate:
            max_rate = rate
        if rate > 10:
            sustained = True
            break

    if not sustained:
        return None

    return Finding(
        pattern="high_velocity",
        severity="warning",
        scope="session",
        summary=f"Sustained {max_rate:.0f} calls/min over 3+ minutes",
        advice=(
            "Pause. Write a detailed prompt. "
            "One good instruction beats 20 vague ones."
        ),
        data={"peak_calls_per_min": round(max_rate, 1)},
    )


def _edit_test_loop(ctx: AnalysisContext) -> Finding | None:
    """Alternating Edit/Bash >5 cycles."""
    clauses, params = _session_clauses(ctx)
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT tool_name FROM tool_call " + where + " "
        "ORDER BY created_at",
        params,
    ).fetchall()

    cycles = 0
    last_was_edit = False
    for r in rows:
        tool = r["tool_name"]
        if tool in ("Edit", "Write") and not last_was_edit:
            last_was_edit = True
        elif tool == "Bash" and last_was_edit:
            cycles += 1
            last_was_edit = False
        elif tool in ("Edit", "Write"):
            last_was_edit = True
        else:
            last_was_edit = False

    if cycles <= 5:
        return None

    return Finding(
        pattern="edit_test_loop",
        severity="warning",
        scope="session",
        summary=f"Edit/Write then Bash cycle detected {cycles}x",
        advice=(
            "Write the full solution in one prompt with test criteria, "
            "don't iterate with trial-and-error."
        ),
        data={"cycle_count": cycles},
    )


def _broad_exploration(ctx: AnalysisContext) -> Finding | None:
    """>10 unique files Read."""
    clauses, params = _session_clauses(ctx)
    clauses.append("tool_name = 'Read'")
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT DISTINCT arguments_json FROM tool_call " + where,
        params,
    ).fetchall()

    unique_files = set()
    for r in rows:
        try:
            args = json.loads(r["arguments_json"])
            path = args.get("file_path", "")
            if path:
                unique_files.add(path)
        except (json.JSONDecodeError, TypeError):
            continue

    if len(unique_files) <= 10:
        return None

    return Finding(
        pattern="broad_exploration",
        severity="info",
        scope="session",
        summary=f"Read {len(unique_files)} unique files",
        advice=(
            "You're exploring broadly. Invest 2 min writing "
            "which files matter and why."
        ),
        data={"unique_files": len(unique_files)},
    )


def _high_todowrite(ctx: AnalysisContext) -> Finding | None:
    """TodoWrite >10% of session calls."""
    counts = _tool_counts(ctx)
    total = sum(counts.values())
    todo = counts.get("TodoWrite", 0)

    if total == 0 or todo == 0:
        return None

    ratio = todo / total
    if ratio <= 0.10:
        return None

    return Finding(
        pattern="high_todowrite",
        severity="warning",
        scope="session",
        summary=f"TodoWrite is {ratio:.0%} of calls ({todo}/{total})",
        advice=(
            "Task tracking is eating calls. If your loop prompt "
            "already defines steps, suppress TodoWrite."
        ),
        data={"todowrite_count": todo, "total_calls": total, "ratio": round(ratio, 2)},
    )


def _low_search_high_read(ctx: AnalysisContext) -> Finding | None:
    """Read >50x with Grep <3x."""
    counts = _tool_counts(ctx)
    reads = counts.get("Read", 0)
    greps = counts.get("Grep", 0)

    if reads <= 50 or greps >= 3:
        return None

    return Finding(
        pattern="low_search_high_read",
        severity="warning",
        scope="session",
        summary=f"Read called {reads}x but Grep only {greps}x",
        advice=(
            "Agent is reading whole files instead of searching. "
            "Add search hints or file:line refs to prompts."
        ),
        data={"read_count": reads, "grep_count": greps},
    )


def _exploration_no_output(ctx: AnalysisContext) -> Finding | None:
    """>20 Read/Glob/Grep with 0 Write/Edit."""
    counts = _tool_counts(ctx)
    explore = (
        counts.get("Read", 0)
        + counts.get("Glob", 0)
        + counts.get("Grep", 0)
    )
    output = counts.get("Write", 0) + counts.get("Edit", 0)

    if explore <= 20 or output > 0:
        return None

    return Finding(
        pattern="exploration_no_output",
        severity="info",
        scope="session",
        summary=f"{explore} exploration calls with 0 output calls",
        advice=(
            "Pure exploration session. Write a brief summarising "
            "findings for a focused follow-up session."
        ),
        data={"exploration_calls": explore, "output_calls": output},
    )


def _large_result_read(ctx: AnalysisContext) -> list[Finding] | None:
    """Large Read results: >100KB once, or >30KB read multiple times."""
    image_exts = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")

    clauses, params = _session_clauses(ctx)
    clauses.append("tool_name = 'Read'")
    clauses.append("result_size > 30000")
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT arguments_json, result_size, COUNT(*) as times "
        "FROM tool_call " + where + " "
        "GROUP BY arguments_json "
        "ORDER BY result_size DESC",
        params,
    ).fetchall()

    if not rows:
        return None

    findings = []
    for r in rows:
        try:
            args = json.loads(r["arguments_json"])
            path = args.get("file_path", "unknown")
        except (json.JSONDecodeError, TypeError):
            path = "unknown"

        # Skip images — handled by _binary_image_reads
        if path.lower().endswith(image_exts):
            continue

        # Single reads under 100KB are fine — only flag repeats or huge files
        if r["times"] <= 1 and r["result_size"] < 100_000:
            continue

        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        size_kb = r["result_size"] / 1024

        findings.append(Finding(
            pattern="large_result_read",
            severity="warning",
            scope="session",
            summary=f"{filename} returns {size_kb:.0f}KB (read {r['times']}x)",
            advice=(
                "Use limit/offset params or create a smaller summary file."
            ),
            data={
                "file": path,
                "result_bytes": r["result_size"],
                "times_read": r["times"],
            },
        ))

    return findings


# ---------------------------------------------------------------------------
# Cache intelligence heuristics (use SessionTokens, not DB)
# ---------------------------------------------------------------------------

def _cache_write_waste(ctx: AnalysisContext) -> Finding | None:
    """Short sessions where cache write premium wasn't recouped."""
    if ctx.tokens is None or ctx.rate is None:
        return None
    t = ctx.tokens
    if t.cache_creation_tokens == 0:
        return None
    if t.llm_call_count >= 10:
        return None
    if t.cache_read_tokens >= t.cache_creation_tokens * 2:
        return None

    from agentmeter.session_reader import cache_savings, calculate_session_cost

    cost = calculate_session_cost(t, ctx.rate)
    write_premium = cost.cache_create_cost - (
        t.cache_creation_tokens * ctx.rate.input_per_mtok / 1_000_000
    )
    saved = cache_savings(t, ctx.rate)

    return Finding(
        pattern="cache_write_waste",
        severity="info",
        scope="session",
        summary=(
            f"{t.llm_call_count} LLM calls — cache write premium "
            f"(${write_premium:.2f}) exceeded read savings (${saved:.2f})"
        ),
        advice=(
            "Short sessions don't benefit from caching. "
            "This isn't actionable — just explains why cost/call is higher."
        ),
        data={
            "llm_calls": t.llm_call_count,
            "cache_write_cost": round(cost.cache_create_cost, 4),
            "cache_read_cost": round(cost.cache_read_cost, 4),
            "cache_creation_tokens": t.cache_creation_tokens,
            "cache_read_tokens": t.cache_read_tokens,
        },
    )


def _low_cache_efficiency(ctx: AnalysisContext) -> Finding | None:
    """High token volume with poor cache hit rate."""
    if ctx.tokens is None:
        return None
    t = ctx.tokens
    if t.llm_call_count < 15:
        return None

    input_total = (
        t.cache_read_tokens + t.cache_creation_tokens + t.input_tokens
    )
    if input_total < 100_000:
        return None

    eff = t.cache_read_tokens / input_total * 100
    if eff >= 40:
        return None

    return Finding(
        pattern="low_cache_efficiency",
        severity="warning",
        scope="session",
        summary=(
            f"Cache efficiency {eff:.0f}% over {t.llm_call_count} LLM calls "
            f"({input_total:,} input tokens mostly uncached)"
        ),
        advice=(
            "Most input tokens aren't hitting cache. Possible causes: "
            "long gaps between turns (>5min cache TTL), "
            "or agent restructuring prompts between calls."
        ),
        data={
            "cache_efficiency": round(eff, 1),
            "llm_calls": t.llm_call_count,
            "input_tokens": t.input_tokens,
            "cache_read_tokens": t.cache_read_tokens,
            "cache_creation_tokens": t.cache_creation_tokens,
        },
    )


# ---------------------------------------------------------------------------
# Cross-session heuristics
# ---------------------------------------------------------------------------

def _repeated_file_cross_session(ctx: AnalysisContext) -> list[Finding] | None:
    """Same file Read >5x across different sessions, with dynamic advice."""
    clauses, params = _session_clauses(ctx)
    clauses.append("tool_name = 'Read'")
    where = build_where(clauses)

    # Get read counts, size stats, and size variance per file
    rows = ctx.conn.execute(
        "SELECT arguments_json, "
        "COUNT(*) as total_reads, "
        "COUNT(DISTINCT session_id) as session_count, "
        "AVG(result_size) as avg_size, "
        "MIN(result_size) as min_size, "
        "MAX(result_size) as max_size "
        "FROM tool_call " + where + " "
        "GROUP BY arguments_json "
        "HAVING COUNT(DISTINCT session_id) > 5 "
        "ORDER BY session_count DESC",
        params,
    ).fetchall()

    if not rows:
        return None

    # Build set of files that were also written/edited
    write_clauses, write_params = _session_clauses(ctx)
    write_clauses.append("tool_name IN ('Write', 'Edit')")
    write_where = build_where(write_clauses)
    write_rows = ctx.conn.execute(
        "SELECT DISTINCT arguments_json FROM tool_call " + write_where,
        write_params,
    ).fetchall()

    written_files: set[str] = set()
    for wr in write_rows:
        try:
            args = json.loads(wr["arguments_json"])
            path = args.get("file_path", "")
            if path:
                written_files.add(path)
        except (json.JSONDecodeError, TypeError):
            continue

    image_exts = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")

    # Extract paths first to detect duplicate basenames
    paths: list[str] = []
    for r in rows:
        try:
            args = json.loads(r["arguments_json"])
            paths.append(args.get("file_path", "unknown"))
        except (json.JSONDecodeError, TypeError):
            paths.append("unknown")

    display_names = _disambiguate_filenames(paths)

    findings = []
    for r, path, display_name in zip(rows, paths, display_names, strict=True):
        avg_size = r["avg_size"] or 0
        is_written = path in written_files
        is_volatile = (r["max_size"] - r["min_size"]) > max(r["min_size"] * 0.1, 100)
        is_image = path.lower().endswith(image_exts)
        is_small = avg_size < 2048

        advice = _classify_repeated_file(
            is_written, is_volatile, is_image, is_small, avg_size,
        )

        findings.append(Finding(
            pattern="repeated_file_cross_session",
            severity="critical",
            scope="cross-session",
            summary=(
                f"{display_name} read {r['total_reads']}x "
                f"across {r['session_count']} sessions"
            ),
            advice=advice,
            data={
                "file": path,
                "display_name": display_name,
                "total_reads": r["total_reads"],
                "session_count": r["session_count"],
                "avg_size": round(avg_size),
                "is_written": is_written,
                "is_volatile": is_volatile,
            },
        ))

    return findings


def _disambiguate_filenames(paths: list[str]) -> list[str]:
    """Add parent directories only to names that collide."""
    from pathlib import PurePosixPath

    parts_list = [PurePosixPath(p).parts for p in paths]
    # Per-path depth: start at 1 (just filename)
    depths = [1] * len(paths)

    for _round in range(10):
        names = []
        for parts, depth in zip(parts_list, depths, strict=True):
            suffix = parts[-depth:] if len(parts) >= depth else parts
            names.append("/".join(suffix))

        # Find which names still collide
        seen: dict[str, list[int]] = {}
        for i, n in enumerate(names):
            seen.setdefault(n, []).append(i)

        dupes = {n: idxs for n, idxs in seen.items() if len(idxs) > 1}
        if not dupes:
            return names

        # Deepen only the colliding entries
        for idxs in dupes.values():
            for i in idxs:
                if depths[i] < len(parts_list[i]):
                    depths[i] += 1

    # Fallback: return what we have
    return [
        "/".join(parts[-d:] if len(parts) >= d else parts)
        for parts, d in zip(parts_list, depths, strict=True)
    ]


def _classify_repeated_file(
    is_written: bool,
    is_volatile: bool,
    is_image: bool,
    is_small: bool,
    avg_size: float,
) -> str:
    """Pick advice based on dynamic file signals."""
    if is_image:
        return "Describe the design intent in text instead of reading the image."

    if is_written or is_volatile:
        return (
            "Agent also modifies this file — can't inline. "
            "Reference the path in CLAUDE.md so it reads once per session."
        )

    if is_small:
        return "Small and stable — inline the whole file into CLAUDE.md."

    size_kb = avg_size / 1024
    return (
        f"Stable but {size_kb:.0f}KB — too large to inline. "
        f"Add a summary to CLAUDE.md covering the key content."
    )


def _binary_image_reads(ctx: AnalysisContext) -> list[Finding] | None:
    """Read of .png/.jpg/.svg with result_size >50KB."""
    clauses, params = _session_clauses(ctx)
    clauses.append("tool_name = 'Read'")
    clauses.append("result_size > 50000")
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT arguments_json, result_size, COUNT(*) as times "
        "FROM tool_call " + where + " "
        "GROUP BY arguments_json "
        "ORDER BY result_size DESC",
        params,
    ).fetchall()

    if not rows:
        return None

    image_exts = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")
    findings = []

    for r in rows:
        try:
            args = json.loads(r["arguments_json"])
            path = args.get("file_path", "")
        except (json.JSONDecodeError, TypeError):
            continue

        if not path.lower().endswith(image_exts):
            continue

        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        size_kb = r["result_size"] / 1024
        inflated_kb = size_kb * 1.33  # base64 inflation

        findings.append(Finding(
            pattern="binary_image_reads",
            severity="warning",
            scope="cross-session",
            summary=(
                f"{filename}: {size_kb:.0f}KB per read "
                f"(~{inflated_kb:.0f}KB in context), read {r['times']}x"
            ),
            advice=(
                "Describe the design intent in text instead. "
                "A text description costs <1KB vs image."
            ),
            data={
                "file": path,
                "result_bytes": r["result_size"],
                "inflated_bytes": round(inflated_kb * 1024),
                "times_read": r["times"],
            },
        ))

    return findings if findings else None


def _session_size_outlier(ctx: AnalysisContext) -> list[Finding] | None:
    """Sessions with >2x the project average call count."""
    clauses, params = _session_clauses(ctx)
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT session_id, COUNT(*) as calls, "
        "MIN(created_at) as started, MAX(created_at) as ended "
        "FROM tool_call " + where + " "
        "GROUP BY session_id",
        params,
    ).fetchall()

    if len(rows) < 3:
        return None

    counts = [r["calls"] for r in rows]
    avg = sum(counts) / len(counts)

    if avg == 0:
        return None

    findings = []
    for r in rows:
        ratio = r["calls"] / avg
        if ratio <= 2.0:
            continue

        # Format date/time from started_at
        started = r["started"] or ""
        when = _format_session_time(started)

        # Calculate duration
        duration = _format_duration(r["started"], r["ended"])

        findings.append(Finding(
            pattern="session_size_outlier",
            severity="warning",
            scope="cross-session",
            summary=(
                f"{when} {duration} — {r['calls']} calls "
                f"({ratio:.1f}x the average of {avg:.0f})"
            ),
            advice=(
                "Split large tasks into sub-specs that complete "
                "in ~60-80 calls."
            ),
            data={
                "session_id": r["session_id"],
                "calls": r["calls"],
                "project_avg": round(avg),
                "ratio": round(ratio, 1),
            },
        ))

    findings.sort(key=lambda f: -f.data["calls"])
    return findings[:5] if findings else None


def _project_concentration(ctx: AnalysisContext) -> Finding | None:
    """One project >60% of total calls."""
    if ctx.project:
        return None  # makes no sense when filtering to one project

    clauses, params = _session_clauses(ctx)
    clauses.append("project != ''")
    where = build_where(clauses)

    rows = ctx.conn.execute(
        "SELECT project, COUNT(*) as cnt "
        "FROM tool_call " + where + " "
        "GROUP BY project "
        "ORDER BY cnt DESC",
        params,
    ).fetchall()

    if len(rows) < 2:
        return None

    total = sum(r["cnt"] for r in rows)
    top = rows[0]
    ratio = top["cnt"] / total if total > 0 else 0

    if ratio <= 0.60:
        return None

    return Finding(
        pattern="project_concentration",
        severity="info",
        scope="cross-session",
        summary=(
            f"{top['project']} is {ratio:.0%} of all calls "
            f"({top['cnt']}/{total})"
        ),
        advice=(
            "If this isn't your top priority, your agent time "
            "doesn't match your stated priorities."
        ),
        data={
            "project": top["project"],
            "calls": top["cnt"],
            "total": total,
            "ratio": round(ratio, 2),
        },
    )
