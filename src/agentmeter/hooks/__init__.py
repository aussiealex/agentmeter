"""Multi-agent hook system for AgentMeter.

Each agent adapter normalises its hook payload into a NormalisedToolEvent,
then passes it to record_event() for DB storage.

Entry points:
    python3 -m agentmeter.hook           # backwards compat (Claude Code)
    python3 -m agentmeter.hooks.claude    # Claude Code PostToolUse
    python3 -m agentmeter.hooks.gemini    # Gemini CLI AfterTool (future)
    python3 -m agentmeter.hooks.codex     # Codex CLI PostToolUse (future)
    python3 -m agentmeter.hooks.copilot   # Copilot CLI postToolUse (future)
    python3 -m agentmeter.hooks.coach     # PreToolUse yellow card coaching
"""
