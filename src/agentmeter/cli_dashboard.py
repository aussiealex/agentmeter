"""Dashboard command — serve the web dashboard on localhost."""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime, timedelta
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import click

from agentmeter.db import MeterDB
from agentmeter.platform import project_name
from agentmeter.session_reader import (
    cache_savings,
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)

WEB_DIR = Path(__file__).parent / "web"


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves static files from web/ and JSON from /api/."""

    def __init__(self, *args, db: MeterDB, **kwargs):  # noqa: N803
        self.db = db
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/projects" or self.path.startswith("/api/projects?"):
            self._api_projects()
        elif self.path == "/api/rates":
            self._api_rates()
        elif self.path == "/api/daily" or self.path.startswith("/api/daily?"):
            self._api_daily()
        elif self.path == "/api/sessions" or self.path.startswith("/api/sessions?"):
            self._api_sessions()
        elif self.path == "/api/overview":
            self._api_overview()
        elif self.path == "/api/strategy":
            self._api_strategy()
        else:
            super().do_GET()

    def _api_projects(self):
        """Return project data as JSON."""
        self._json_response(build_projects_data(self.db))

    def _api_daily(self):
        """Return daily totals with cost as JSON."""
        self._json_response(build_daily_data(self.db))

    def _api_sessions(self):
        """Return session list with cost as JSON."""
        self._json_response(build_sessions_data(self.db))

    def _api_overview(self):
        """Return overview KPIs as JSON."""
        self._json_response(build_overview_data(self.db))

    def _api_strategy(self):
        """Return strategy data — budgets, breakers, recommendations."""
        self._json_response(build_strategy_data(self.db))

    def _api_rates(self):
        """Return rate card as JSON."""
        rates = self.db.get_all_rates()
        data = [
            {
                "modelId": r.model_id,
                "displayName": r.display_name,
                "input": r.input_per_mtok,
                "output": r.output_per_mtok,
                "cacheRead": r.cached_per_mtok,
                "cacheWrite": r.cache_write_per_mtok,
            }
            for r in rates
        ]
        self._json_response(data)

    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        """Suppress request logs."""


def build_projects_data(db: MeterDB, days: int = 30) -> dict:
    """Build the projects JSON payload from real DB data."""
    since = (datetime.now() - timedelta(days=days)).isoformat()

    # Get project stats
    project_stats = db.get_project_stats(since=since)
    sessions = db.get_sessions(limit=500)

    # Build per-project data
    projects = []
    total_cost = 0.0
    total_sessions = 0

    # Group sessions by project
    project_sessions: dict[str, list] = {}
    for s in sessions:
        cmd = s.server_command or ""
        proj = project_name(cmd)
        if not proj:
            continue
        project_sessions.setdefault(proj, []).append(s)

    # Get tool breakdown per project
    project_tools = {}
    for ps in project_stats:
        if ps.project:
            project_tools[ps.project] = db.get_project_tool_breakdown(
                ps.project, since=since,
            )

    for ps in project_stats:
        if not ps.project:
            continue

        sess_list = project_sessions.get(ps.project, [])
        if not sess_list:
            continue

        # Compute real cost from session JSONL
        proj_cost = 0.0
        cache_read_cost = 0.0
        output_cost = 0.0
        input_cost = 0.0
        total_tokens = 0
        cache_read_tokens = 0
        total_input_side = 0
        proj_saved = 0.0
        llm_calls = 0
        session_count = 0
        commits = 0
        files_changed = 0
        tests_passed = 0
        tests_failed = 0

        for s in sess_list:
            jsonl_path = find_session_jsonl(s.id, s.server_command)
            if not jsonl_path:
                continue

            tokens = read_session_tokens_from_file(jsonl_path)
            if not tokens or tokens.llm_call_count == 0:
                continue

            rate = db.get_rate(tokens.model_id)
            if not rate:
                continue

            cost_data = calculate_session_cost(tokens, rate)
            proj_cost += cost_data.total_cost
            cache_read_cost += cost_data.cache_read_cost
            output_cost += cost_data.output_cost
            input_cost += cost_data.input_cost + cost_data.cache_create_cost
            total_tokens += (
                tokens.input_tokens + tokens.cache_creation_tokens
                + tokens.cache_read_tokens + tokens.output_tokens
            )
            cache_read_tokens += tokens.cache_read_tokens
            total_input_side += (
                tokens.cache_read_tokens
                + tokens.cache_creation_tokens
                + tokens.input_tokens
            )
            proj_saved += cache_savings(tokens, rate)
            llm_calls += tokens.llm_call_count
            session_count += 1
            commits += s.commits
            files_changed += s.files_changed
            tests_passed += s.tests_passed
            tests_failed += s.tests_failed

        if session_count == 0:
            continue

        # Last run
        last_session = max(sess_list, key=lambda s: s.started_at)
        last_run = _relative_time(last_session.started_at)

        # Tool breakdown
        tools = []
        tool_rows = project_tools.get(ps.project, [])
        for t in tool_rows[:3]:
            tools.append({
                "name": t.tool_name,
                "calls": t.call_count,
                "bytes": t.total_result_size,
            })
        remaining_calls = sum(t.call_count for t in tool_rows[3:])
        remaining_bytes = sum(t.total_result_size for t in tool_rows[3:])
        total_tool_calls = sum(t.call_count for t in tool_rows)
        total_tool_bytes = sum(t.total_result_size for t in tool_rows)

        cache_pct = (cache_read_cost / proj_cost * 100) if proj_cost > 0 else 0
        output_pct = (output_cost / proj_cost * 100) if proj_cost > 0 else 0
        input_pct = (input_cost / proj_cost * 100) if proj_cost > 0 else 0
        cache_eff = (
            cache_read_tokens / total_input_side * 100
        ) if total_input_side > 0 else 0

        projects.append({
            "name": ps.project,
            "sessions": session_count,
            "lastRun": last_run,
            "cost": round(proj_cost, 2),
            "costSplit": {
                "cache": round(cache_read_cost, 2),
                "cachePct": round(cache_pct, 1),
                "output": round(output_cost, 2),
                "outputPct": round(output_pct, 1),
                "input": round(input_cost, 2),
                "inputPct": round(input_pct, 1),
            },
            "tokens": total_tokens,
            "cacheEfficiency": round(cache_eff, 1),
            "cacheSaved": round(proj_saved, 2),
            "llmCalls": llm_calls,
            "tools": tools,
            "moreTools": {
                "count": len(tool_rows) - 3 if len(tool_rows) > 3 else 0,
                "calls": remaining_calls,
                "bytes": remaining_bytes,
            },
            "totalToolCalls": total_tool_calls,
            "totalToolBytes": total_tool_bytes,
            "outcomes": {
                "commits": commits,
                "filesChanged": files_changed,
                "testsPassed": tests_passed,
                "testsFailed": tests_failed,
                "costPerCommit": round(
                    proj_cost / commits, 2,
                ) if commits > 0 else 0,
                "costPerTest": round(
                    proj_cost / tests_passed, 2,
                ) if tests_passed > 0 else 0,
            },
        })

        total_cost += proj_cost
        total_sessions += session_count

    # Sort by cost descending
    projects.sort(key=lambda p: p["cost"], reverse=True)

    # Add share percentages
    for p in projects:
        p["sharePct"] = round(
            p["cost"] / total_cost * 100, 1,
        ) if total_cost > 0 else 0

    return {
        "projects": projects,
        "totalCost": round(total_cost, 2),
        "totalSessions": total_sessions,
        "window": {
            "start": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
            "end": datetime.now().strftime("%Y-%m-%d"),
        },
    }


def build_strategy_data(db: MeterDB) -> dict:
    """Build strategy data — budgets, breakers, recommendations."""
    budgets = db.get_budgets()
    breakers = db.get_breakers()
    trips = db.get_breaker_trips(limit=10)

    proj_data = build_projects_data(db)
    ps = proj_data["projects"]

    # Generate recommendations from project data
    recs = []

    # Per-project recommendations
    for p in ps:
        cs = p["costSplit"]
        name = p["name"]
        sessions = p["sessions"]
        if sessions == 0:
            continue

        avg_calls = p.get("llmCalls", 0) / sessions
        o = p["outcomes"]

        # Session splitting
        if avg_calls > 200:
            saving = int(p["cost"] * 0.25)
            recs.append(
                f"{name}: avg {int(avg_calls)} LLM calls/"
                f"session. Splitting at ~100 could save "
                f"~${saving} (25% of spend)."
            )

        # Cache dominance
        if cs["cachePct"] > 90 and p["cost"] > 50:
            recs.append(
                f"{name}: cache is {cs['cachePct']}% of cost. "
                f"Each turn replays the entire conversation — "
                f"split long sessions with handoff briefs."
            )

        # Tool-specific: large data tools
        for t in p["tools"]:
            if t["name"] == "Bash" and t["bytes"] > 500_000:
                recs.append(
                    f"{name}: Bash produced "
                    f"{_fmt_bytes(t['bytes'])} of output. "
                    f"Pipe through tail/head to avoid "
                    f"polluting context."
                )
            if t["name"] == "Read" and t["calls"] > 50:
                recs.append(
                    f"{name}: {t['calls']} Read calls "
                    f"pulled {_fmt_bytes(t['bytes'])}. "
                    f"Each read inflates context. Use line "
                    f"offsets to read only what you need."
                )

        # High cost per commit
        if o["commits"] > 0 and o["costPerCommit"] > 50:
            recs.append(
                f"{name}: ${o['costPerCommit']:.0f}/commit. "
                f"Write detailed prompts with acceptance "
                f"criteria to reduce iterations."
            )

        # Sessions with no outcome
        if o["commits"] == 0 and o["testsPassed"] == 0 and p["cost"] > 20:
                recs.append(
                    f"{name}: ${p['cost']:.0f} spent with no "
                    f"commits or test results. Consider "
                    f"setting clearer task goals upfront."
                )

    # Global recommendations
    if not budgets:
        recs.append(
            "No budget rules set. Use "
            "'agentmeter budget set daily 200' to cap "
            "daily tool calls and catch runaway sessions."
        )
    if not breakers:
        recs.append(
            "No circuit breakers configured. Use "
            "'agentmeter breaker set 20 60' to auto-trip "
            "when call velocity spikes (20 calls in 60s)."
        )

    # Single project dominance
    if ps and ps[0]["sharePct"] > 60:
        recs.append(
            f"{ps[0]['name']} is {ps[0]['sharePct']}% of "
            f"total spend. Review whether session lengths "
            f"are proportionate to task complexity."
        )

    return {
        "budgets": [
            {
                "scope": b.scope,
                "serverName": b.server_name or "all",
                "maxCalls": b.max_calls,
                "action": b.action,
            }
            for b in budgets
        ],
        "breakers": [
            {
                "serverName": b.server_name or "all",
                "maxCalls": b.max_calls,
                "windowSeconds": b.window_seconds,
                "cooldownSeconds": b.cooldown_seconds,
            }
            for b in breakers
        ],
        "recentTrips": [
            {
                "serverName": t.server_name,
                "callCount": t.call_count,
                "windowSeconds": t.window_seconds,
                "trippedAt": t.tripped_at,
            }
            for t in trips
        ],
        "recommendations": recs,
    }


def build_overview_data(db: MeterDB) -> dict:
    """Build overview KPIs from project and session data."""
    proj_data = build_projects_data(db)
    ps = proj_data["projects"]

    total_cost = proj_data["totalCost"]
    total_sessions = proj_data["totalSessions"]
    total_commits = sum(p["outcomes"]["commits"] for p in ps)
    total_tests = sum(p["outcomes"]["testsPassed"] for p in ps)
    cost_per_commit = (
        round(total_cost / total_commits, 2)
        if total_commits > 0 else 0
    )

    # Top project
    top_proj = ps[0]["name"] if ps else "—"
    top_pct = ps[0]["sharePct"] if ps else 0

    # Daily average
    daily_data = build_daily_data(db)
    days_with_cost = [
        d for d in daily_data["days"] if d["cost"] > 0
    ]
    daily_avg = (
        round(total_cost / len(days_with_cost), 2)
        if days_with_cost else 0
    )

    # Projected EOM
    now = datetime.now()
    day_of_month = now.day
    days_in_month = 31  # rough
    projected = round(
        total_cost / day_of_month * days_in_month, 2,
    ) if day_of_month > 0 else 0

    return {
        "totalCost": total_cost,
        "totalSessions": total_sessions,
        "totalCommits": total_commits,
        "totalTests": total_tests,
        "costPerCommit": cost_per_commit,
        "dailyAvg": daily_avg,
        "projected": projected,
        "topProject": top_proj,
        "topProjectPct": top_pct,
        "projectCount": len(ps),
        "projects": [
            {
                "name": p["name"],
                "cost": p["cost"],
                "sharePct": p["sharePct"],
                "sessions": p["sessions"],
            }
            for p in ps
        ],
    }


def build_sessions_data(db: MeterDB) -> dict:
    """Build session list with cost from real token data."""
    sessions = db.get_sessions(limit=50)
    session_stats = db.get_session_stats(limit=50)
    stats_map = {s.session_id: s for s in session_stats}

    rows = []
    for s in sessions:
        cmd = s.server_command or ""
        project = project_name(cmd)

        # Real cost
        cost_val = 0.0
        tokens_total = 0
        model = ""
        llm_calls = 0
        jsonl_path = find_session_jsonl(s.id, s.server_command)
        if jsonl_path:
            tokens = read_session_tokens_from_file(jsonl_path)
            if tokens and tokens.llm_call_count > 0:
                rate = db.get_rate(tokens.model_id)
                if rate:
                    cost_data = calculate_session_cost(tokens, rate)
                    cost_val = cost_data.total_cost
                tokens_total = (
                    tokens.input_tokens
                    + tokens.cache_creation_tokens
                    + tokens.cache_read_tokens
                    + tokens.output_tokens
                )
                model = tokens.model_id
                llm_calls = tokens.llm_call_count

        # Tool call count from stats
        ss = stats_map.get(s.id)
        tool_calls = ss.total_calls if ss else 0

        # Duration
        duration = ""
        if s.started_at and s.ended_at:
            try:
                start = datetime.fromisoformat(s.started_at)
                end = datetime.fromisoformat(s.ended_at)
                mins = int((end - start).total_seconds() / 60)
                duration = f"{mins}m"
            except (ValueError, TypeError):
                pass

        # Outcome
        outcome = ""
        if s.commits:
            outcome = f"{s.commits} commits"
        if s.tests_passed:
            outcome += (", " if outcome else "")
            outcome += f"{s.tests_passed} passed"
        if s.tests_failed:
            outcome += f", {s.tests_failed} failed"

        rows.append({
            "id": s.id[:12],
            "fullId": s.id,
            "project": project,
            "model": model,
            "duration": duration,
            "toolCalls": tool_calls,
            "llmCalls": llm_calls,
            "tokens": tokens_total,
            "cost": round(cost_val, 2),
            "started": s.started_at[:19].replace("T", " ")
            if s.started_at else "",
            "outcome": outcome,
            "hasOutcome": bool(
                s.commits or s.tests_passed,
            ),
        })

    return {"sessions": rows}


def build_daily_data(db: MeterDB, days: int = 14) -> dict:
    """Build daily totals with cost from real token data."""
    totals = db.get_daily_totals(days=days)
    sessions = db.get_sessions(limit=500)

    # Build daily cost map
    daily_costs: dict[str, float] = {}
    daily_sessions: dict[str, int] = {}
    for session in sessions:
        jsonl_path = find_session_jsonl(
            session.id, session.server_command,
        )
        if not jsonl_path:
            continue
        tokens = read_session_tokens_from_file(jsonl_path)
        if not tokens or tokens.llm_call_count == 0:
            continue
        rate = db.get_rate(tokens.model_id)
        if not rate:
            continue
        cost_data = calculate_session_cost(tokens, rate)
        day = session.started_at[:10]
        daily_costs[day] = daily_costs.get(day, 0) + cost_data.total_cost
        daily_sessions[day] = daily_sessions.get(day, 0) + 1

    rows = []
    for t in totals:
        rows.append({
            "day": t.day,
            "calls": t.call_count,
            "errors": t.error_count,
            "cost": round(daily_costs.get(t.day, 0), 2),
            "sessions": daily_sessions.get(t.day, 0),
        })

    return {
        "days": rows,
        "totalCost": round(sum(daily_costs.values()), 2),
    }


def _fmt_bytes(b: int) -> str:
    """Format bytes to human-readable string."""
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b / 1_000_000:.1f} MB"
    if b >= 1_000:
        return f"{b / 1_000:.1f} KB"
    return f"{b} B"


def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative string like '2h ago'."""
    try:
        dt = datetime.fromisoformat(iso_str)
        delta = datetime.now() - dt
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}m ago"
        if hours < 24:
            return f"{int(hours)}h ago"
        days = int(hours / 24)
        return f"{days}d ago"
    except (ValueError, TypeError):
        return "—"


@click.command()
@click.option("--port", "-p", default=8070, help="Port to serve on.")
@click.option("--no-open", is_flag=True, help="Don't open browser automatically.")
def dashboard(port: int, no_open: bool) -> None:
    """Open the AgentMeter web dashboard."""
    db = MeterDB()
    handler = partial(DashboardHandler, db=db)
    server = HTTPServer(("127.0.0.1", port), handler)

    url = f"http://127.0.0.1:{port}"
    click.echo(f"  AgentMeter dashboard → {url}")
    click.echo("  Press Ctrl+C to stop.\n")

    if not no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\n  Stopped.")
    finally:
        db.close()
        server.server_close()
