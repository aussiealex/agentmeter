"""Strategy command — per-project cost analysis with actionable advice."""

from __future__ import annotations

from datetime import datetime, timedelta

import click

from agentmeter.db import MeterDB
from agentmeter.models import RateCard, SessionCost, SessionTokens, ToolStats
from agentmeter.platform import project_name
from agentmeter.session_reader import (
    cache_savings,
    calculate_session_cost,
    find_session_jsonl,
    read_session_tokens_from_file,
)

# Project-to-role mapping from business-thesis.md.
_PROJECT_ROLES: dict[str, tuple[str, int]] = {
    "complyit": ("Revenue Engine", 1),
    "securityconsultancy": ("Revenue Engine", 1),
    "security-consultancy": ("Revenue Engine", 1),
    "agentmeter": ("Long-Term Bet", 2),
    "ralph_modey": ("Long-Term Bet", 2),
    "modeyapp": ("Long-Term Bet", 2),
    "policyguardian": ("Moat Builder", 3),
    "deviceguardian": ("Moat Builder", 3),
    "mailsift": ("Moat Builder", 3),
    "ralph": ("Moat Builder", 3),
    "politicks": ("Personal", 4),
    "cybercheck": ("Personal", 4),
    "cryptobot": ("Personal", 4),
    "crypto-bot": ("Personal", 4),
    "boopmynose": ("Personal", 4),
    "boop": ("Personal", 4),
    "saelection": ("Personal", 4),
    "unisite": ("Personal", 4),
    "smallshield": ("Personal", 4),
}


def _classify(project: str) -> tuple[str, int]:
    """Map a project name to (role, priority)."""
    key = (project.lower()
           .replace(" ", "").replace("_", "").replace("-", ""))
    if key in _PROJECT_ROLES:
        return _PROJECT_ROLES[key]
    for known, info in _PROJECT_ROLES.items():
        cleaned = known.replace("-", "").replace("_", "")
        if cleaned in key or key in cleaned:
            return info
    return ("Unknown", 5)


class _SessionData:
    """Token and cost data for one session."""

    __slots__ = ("cost", "tokens", "session_id", "rate")

    def __init__(
        self, session_id: str, tokens: SessionTokens,
        cost: SessionCost, rate: RateCard,
    ) -> None:
        self.session_id = session_id
        self.tokens = tokens
        self.cost = cost
        self.rate = rate


class _ProjectData:
    """All data for one project."""

    __slots__ = (
        "name", "role", "priority", "tools",
        "sessions", "call_count",
        "commits", "files_changed",
        "tests_passed", "tests_failed",
    )

    def __init__(
        self, *, name: str, role: str, priority: int,
        tools: list[ToolStats], call_count: int,
    ) -> None:
        self.name = name
        self.role = role
        self.priority = priority
        self.tools = tools
        self.call_count = call_count
        self.sessions: list[_SessionData] = []
        self.commits = 0
        self.files_changed = 0
        self.tests_passed = 0
        self.tests_failed = 0

    @property
    def total_cost(self) -> float:
        return sum(s.cost.total_cost for s in self.sessions)

    @property
    def cache_read_cost(self) -> float:
        return sum(s.cost.cache_read_cost for s in self.sessions)

    @property
    def output_cost(self) -> float:
        return sum(s.cost.output_cost for s in self.sessions)

    @property
    def input_cost(self) -> float:
        return sum(
            s.cost.input_cost + s.cost.cache_create_cost
            for s in self.sessions
        )

    @property
    def total_llm_calls(self) -> int:
        return sum(s.tokens.llm_call_count for s in self.sessions)

    @property
    def avg_session_cost(self) -> float:
        n = len(self.sessions)
        return self.total_cost / n if n else 0

    @property
    def avg_session_llm_calls(self) -> float:
        n = len(self.sessions)
        return self.total_llm_calls / n if n else 0

    @property
    def cost_per_llm_call(self) -> float:
        t = self.total_llm_calls
        return self.total_cost / t if t else 0

    @property
    def cache_read_pct(self) -> float:
        t = self.total_cost
        return (self.cache_read_cost / t * 100) if t > 0 else 0

    @property
    def total_cache_savings(self) -> float:
        return sum(
            cache_savings(s.tokens, s.rate) for s in self.sessions
        )

    @property
    def aggregate_cache_efficiency(self) -> float | None:
        total_read = sum(s.tokens.cache_read_tokens for s in self.sessions)
        total_input = sum(
            s.tokens.cache_read_tokens
            + s.tokens.cache_creation_tokens
            + s.tokens.input_tokens
            for s in self.sessions
        )
        if total_input == 0:
            return None
        return total_read / total_input * 100


@click.command()
@click.option(
    "--days", "-d", default=7,
    help="Days of history to analyse.",
)
def strategy(days: int) -> None:
    """Analyse per-project agent costs and advise on reducing spend.

    Uses real token data from Claude Code session transcripts to
    show exactly where money goes in each project — cache reads,
    output, session length — and gives specific recommendations.

    \b
    What it shows per project:
      - Real dollar cost and % of total spend
      - Cache read vs output vs input cost split
      - Average session length and cost per LLM call
      - Heaviest tools by data volume
      - Specific cost-cutting advice

    Examples:

    \b
        agentmeter strategy
        agentmeter strategy --days 30
    """
    db = MeterDB()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    project_stats = db.get_project_stats(since=since)

    if not project_stats:
        click.echo()
        click.echo("  No project-tagged tool calls in the "
                    f"last {days} days.")
        click.echo("  Project tagging requires hook-based "
                    "metering (agentmeter hook install claude).")
        click.echo()
        db.close()
        return

    # Build project data with tool breakdowns
    proj_map: dict[str, _ProjectData] = {}
    for ps in project_stats:
        role, priority = _classify(ps.project)
        tools = db.get_project_tool_breakdown(
            ps.project, since=since,
        )
        pd = _ProjectData(
            name=ps.project, role=role, priority=priority,
            tools=tools, call_count=ps.call_count,
        )
        proj_map[ps.project] = pd

    # Attach real session cost + outcome data
    sessions = db.get_sessions(limit=500)
    for session in sessions:
        if session.started_at < since:
            continue
        project = project_name(session.server_command)
        if project in proj_map:
            pd = proj_map[project]
            pd.commits += session.commits
            pd.files_changed += session.files_changed
            pd.tests_passed += session.tests_passed
            pd.tests_failed += session.tests_failed

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
        if project in proj_map:
            proj_map[project].sessions.append(
                _SessionData(session.id, tokens, cost_data, rate),
            )

    db.close()

    projects = sorted(
        proj_map.values(), key=lambda p: -p.total_cost,
    )
    total_cost = sum(p.total_cost for p in projects)
    total_calls = sum(p.call_count for p in projects)

    click.echo()
    click.echo(f"  Strategy Report (last {days} days)")
    click.echo(f"  {'=' * 58}")

    if total_cost > 0:
        click.echo(
            f"  Total: ${total_cost:.2f} across "
            f"{len(projects)} projects, "
            f"{total_calls} tool calls",
        )
    else:
        click.echo(
            f"  {total_calls} tool calls across "
            f"{len(projects)} projects "
            f"(no token cost data available)",
        )
    click.echo()

    # Per-project breakdown
    for p in projects:
        _print_project(p, total_cost, total_calls)

    # Recommendations
    _print_recommendations(projects, total_cost)


def _print_project(
    p: _ProjectData, total_cost: float, total_calls: int,
) -> None:
    pct = (p.total_cost / total_cost * 100) if total_cost > 0 else 0
    cost_str = f"${p.total_cost:.2f}" if p.total_cost > 0 else (
        "no cost data"
    )

    click.echo(
        f"  {p.name}  —  {cost_str}  ({pct:.0f}%)  "
        f"[{p.role} #{p.priority}]",
    )

    if p.total_cost > 0:
        bar_len = int(pct / 2)
        click.echo(f"    {'█' * max(bar_len, 1)}")

    # Session economics
    n_sess = len(p.sessions)
    click.echo(
        f"    {p.call_count} tool calls, {n_sess} sessions",
    )

    if n_sess > 0:
        click.echo(
            f"    Avg session: {p.avg_session_llm_calls:.0f} "
            f"LLM calls, ${p.avg_session_cost:.2f}",
        )
        click.echo(
            f"    Cost/LLM call: ${p.cost_per_llm_call:.4f}",
        )

        # Cost split
        click.echo(
            f"    Cost split: cache reads "
            f"{p.cache_read_pct:.0f}%"
            f" | output "
            f"{_pct(p.output_cost, p.total_cost):.0f}%"
            f" | input "
            f"{_pct(p.input_cost, p.total_cost):.0f}%",
        )

        # Cache intelligence
        eff = p.aggregate_cache_efficiency
        if eff is not None and eff > 0:
            click.echo(f"    Cache efficiency: {eff:.0f}%")
        saved = p.total_cache_savings
        if saved > 0.01:
            hypothetical = p.total_cost + saved
            saved_pct = saved / hypothetical * 100
            click.echo(
                f"    Cache saved: ${saved:.2f} "
                f"({saved_pct:.0f}% less than without caching)"
            )

        # Outlier detection
        if n_sess >= 2:
            costs = sorted(
                s.cost.total_cost for s in p.sessions
            )
            cheapest = costs[0]
            most_expensive = costs[-1]
            if most_expensive > cheapest * 3 and most_expensive > 1:
                click.echo(
                    f"    Outlier: costliest session "
                    f"${most_expensive:.2f} vs cheapest "
                    f"${cheapest:.2f} "
                    f"({most_expensive / cheapest:.0f}x)",
                )

    # Outcomes
    has_outcomes = (
        p.commits or p.tests_passed or p.tests_failed
    )
    if has_outcomes:
        parts = []
        if p.commits:
            parts.append(f"{p.commits} commits")
            if p.total_cost > 0 and p.commits > 0:
                cpc = p.total_cost / p.commits
                parts.append(f"${cpc:.2f}/commit")
        if p.files_changed:
            parts.append(f"{p.files_changed} files changed")
        if p.tests_passed:
            parts.append(f"{p.tests_passed} tests passed")
        if p.tests_failed:
            parts.append(f"{p.tests_failed} tests failed")
        click.echo(f"    Outcomes: {', '.join(parts)}")

    # Tool breakdown (top 5 by data volume)
    if p.tools:
        total_bytes = sum(t.total_result_size for t in p.tools)
        click.echo("    Top tools (by data volume):")
        for t in p.tools[:5]:
            byte_pct = (
                t.total_result_size / total_bytes * 100
            ) if total_bytes > 0 else 0
            click.echo(
                f"      {t.tool_name:<20} {t.call_count:>4}x  "
                f"{_fmt_bytes(t.total_result_size):>8}  "
                f"{byte_pct:>3.0f}%",
            )

    click.echo()


def _print_recommendations(
    projects: list[_ProjectData], total_cost: float,
) -> None:
    recs: list[str] = []

    for p in projects:
        if not p.sessions:
            continue

        # Session length advice
        if p.avg_session_llm_calls > 150:
            # Estimate savings from shorter sessions
            # Cache cost scales ~quadratically with session length
            # because each turn re-sends the full history.
            # Halving session length cuts cache by ~40-50%.
            cache_cost = p.cache_read_cost
            est_savings = cache_cost * 0.35
            if est_savings > 0.50:
                recs.append(
                    f"{p.name}: sessions average "
                    f"{p.avg_session_llm_calls:.0f} LLM calls. "
                    f"Splitting at ~100 calls would reduce "
                    f"cache re-reads — estimated saving "
                    f"~${est_savings:.0f} "
                    f"({_pct(est_savings, p.total_cost):.0f}% "
                    f"of {p.name} spend).",
                )

        # Cache dominance advice
        if p.cache_read_pct > 85 and p.total_cost > 1:
            recs.append(
                f"{p.name}: {p.cache_read_pct:.0f}% of cost "
                f"is cache reads (conversation history "
                f"re-sent every turn). Shorter sessions and "
                f"smaller CLAUDE.md reduce this directly.",
            )

        # Output ratio — if very low, context bloat
        if p.total_cost > 1:
            output_pct = _pct(p.output_cost, p.total_cost)
            if output_pct < 2:
                recs.append(
                    f"{p.name}: output is only "
                    f"{output_pct:.1f}% of cost. Most spend "
                    f"is re-reading context, not generating. "
                    f"System prompt size affects every turn.",
                )

        # High-volume tool-specific advice
        for t in p.tools[:3]:
            if (t.tool_name == "Read"
                    and t.call_count > 50
                    and t.total_result_size > 500_000):
                recs.append(
                    f"{p.name}: {t.call_count} Read calls "
                    f"pulled {_fmt_bytes(t.total_result_size)}."
                    f" Each read inflates context for all "
                    f"subsequent turns. Use line offsets to "
                    f"read only what you need.",
                )
                break
            if (t.tool_name == "Bash"
                    and t.total_result_size > 200_000):
                recs.append(
                    f"{p.name}: Bash produced "
                    f"{_fmt_bytes(t.total_result_size)} of "
                    f"output. Pipe through tail/head or "
                    f"redirect to file to avoid polluting "
                    f"context.",
                )
                break
            if (t.tool_name == "Agent"
                    and t.call_count > 10):
                recs.append(
                    f"{p.name}: {t.call_count} Agent "
                    f"subagent calls. Each spawns a new "
                    f"context. Check if direct Grep/Glob "
                    f"would suffice for simple lookups.",
                )
                break

    # Cost-per-call comparison across projects
    projects_with_cost = [
        p for p in projects if p.total_llm_calls > 0
    ]
    if len(projects_with_cost) >= 2:
        by_cpc = sorted(
            projects_with_cost,
            key=lambda p: -p.cost_per_llm_call,
        )
        most = by_cpc[0]
        least = by_cpc[-1]
        if most.cost_per_llm_call > least.cost_per_llm_call * 2:
            recs.append(
                f"{most.name} costs "
                f"${most.cost_per_llm_call:.4f}/LLM call vs "
                f"{least.name} at "
                f"${least.cost_per_llm_call:.4f}. "
                f"The difference is likely session length — "
                f"longer sessions accumulate more cache per "
                f"turn.",
            )

    # Priority misalignment
    if total_cost > 0:
        role_costs: dict[str, float] = {}
        for p in projects:
            role_costs[p.role] = (
                role_costs.get(p.role, 0) + p.total_cost
            )
        personal_cost = role_costs.get("Personal", 0)
        personal_pct = personal_cost / total_cost * 100
        if personal_pct > 30:
            recs.append(
                f"Personal projects are {personal_pct:.0f}% "
                f"of spend (${personal_cost:.2f}). "
                f"Fine if intentional.",
            )

    if recs:
        click.echo(f"  {'─' * 58}")
        click.echo("  Recommendations:")
        for i, rec in enumerate(recs, 1):
            click.echo(f"    {i}. {rec}")
        click.echo()
    elif projects:
        click.echo(f"  {'─' * 58}")
        click.echo("  No specific recommendations — spend "
                    "looks reasonable.")
        click.echo()


def _pct(part: float, total: float) -> float:
    return (part / total * 100) if total > 0 else 0


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
