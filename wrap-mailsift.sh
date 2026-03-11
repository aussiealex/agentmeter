#!/bin/bash
# Wrapper script for debugging MCP proxy startup
exec /media/aa/LargeBackup/MainApps/AgentMeter/.venv/bin/agentmeter wrap --name mailsift /media/aa/LargeBackup/MainApps/MailSift/.venv/bin/python3 -m mailsift.mcp.server 2>/tmp/agentmeter.log
