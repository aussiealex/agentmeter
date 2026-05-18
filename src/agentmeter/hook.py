"""Backwards-compatible entry point for Claude Code PostToolUse hook.

Users have `python3 -m agentmeter.hook` in their ~/.claude/settings.json.
This shim delegates to the new hooks.claude module.
"""

from agentmeter.hooks.claude import main

if __name__ == "__main__":
    main()
