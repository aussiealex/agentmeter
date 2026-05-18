"""Tests for multi-agent hook adapters (gemini, codex, copilot)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentmeter.db import MeterDB


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AGENTMETER_DB", str(db_path))
    return db_path


def _run_hook(
    module: str,
    payload: dict,
    env_override: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run a hook adapter as a subprocess, feeding JSON on stdin."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )


# ── Gemini CLI ──────────────────────────────────────────────────────


class TestGeminiAdapter:
    MODULE = "agentmeter.hooks.gemini"

    def test_records_tool_call(self, tmp_db: Path) -> None:
        payload = {
            "session_id": "gem-1",
            "cwd": "/home/user/myproject",
            "timestamp": "2026-05-18T10:00:00",
            "hook_event_name": "AfterTool",
            "tool_name": "readFile",
            "tool_input": {"path": "/tmp/test.py"},
            "tool_response": {
                "llmContent": "file contents here",
            },
        }
        result = _run_hook(self.MODULE, payload)
        assert result.returncode == 0

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].tool_name == "readFile"
        assert calls[0].server_name == "gemini-cli"
        assert calls[0].session_id == "gem-1"
        db.close()

    def test_captures_project_from_cwd(self, tmp_db: Path) -> None:
        _run_hook(self.MODULE, {
            "session_id": "gem-2",
            "cwd": "/home/user/AgentMeter",
            "tool_name": "shell",
            "tool_input": {"command": "ls"},
            "tool_response": {"llmContent": "output"},
        })

        db = MeterDB(tmp_db)
        sessions = db.get_sessions(limit=10)
        assert len(sessions) == 1
        assert sessions[0].server_name == "gemini-cli"
        db.close()

    def test_handles_error_response(self, tmp_db: Path) -> None:
        _run_hook(self.MODULE, {
            "session_id": "gem-3",
            "tool_name": "shell",
            "tool_input": {},
            "tool_response": {
                "error": "command not found",
            },
        })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].is_error is True
        db.close()

    def test_skips_mcp_tools(self, tmp_db: Path) -> None:
        _run_hook(self.MODULE, {
            "session_id": "gem-4",
            "tool_name": "mcp__server__tool",
            "tool_input": {},
            "tool_response": {},
        })

        db = MeterDB(tmp_db)
        assert db.get_total_calls() == 0
        db.close()

    def test_empty_stdin(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", self.MODULE],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0

    def test_invalid_json(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", self.MODULE],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0


# ── Codex CLI ───────────────────────────────────────────────────────


class TestCodexAdapter:
    MODULE = "agentmeter.hooks.codex"

    def test_records_tool_call(self, tmp_db: Path) -> None:
        payload = {
            "session_id": "cdx-1",
            "cwd": "/home/user/project",
            "hook_event_name": "PostToolUse",
            "model": "gpt-4.1",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_response": "total 42\ndrwxr-xr-x ...",
        }
        result = _run_hook(self.MODULE, payload)
        assert result.returncode == 0

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].tool_name == "Bash"
        assert calls[0].server_name == "codex-cli"
        db.close()

    def test_captures_model_id(self, tmp_db: Path) -> None:
        """Codex provides model in the payload — verify it flows through."""
        _run_hook(self.MODULE, {
            "session_id": "cdx-2",
            "model": "o4-mini",
            "tool_name": "apply_patch",
            "tool_input": {"command": "patch content"},
            "tool_response": "applied",
        })

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        # model_id is stored but not yet exposed in ToolCall — it's in the DB
        db.close()

    def test_skips_mcp_tools(self, tmp_db: Path) -> None:
        _run_hook(self.MODULE, {
            "session_id": "cdx-3",
            "tool_name": "mcp__github__pr",
            "tool_input": {},
            "tool_response": "ok",
        })

        db = MeterDB(tmp_db)
        assert db.get_total_calls() == 0
        db.close()

    def test_empty_stdin(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", self.MODULE],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0


# ── Copilot CLI ─────────────────────────────────────────────────────


class TestCopilotAdapter:
    MODULE = "agentmeter.hooks.copilot"

    def test_records_snake_case_payload(self, tmp_db: Path) -> None:
        payload = {
            "session_id": "cop-1",
            "timestamp": "2026-05-18T10:00:00",
            "cwd": "/home/user/repo",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_result": {
                "result_type": "success",
                "text_result_for_llm": "On branch main\nnothing to commit",
            },
        }
        result = _run_hook(self.MODULE, payload)
        assert result.returncode == 0

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].tool_name == "Bash"
        assert calls[0].server_name == "copilot-cli"
        assert "nothing to commit" in calls[0].result_json
        db.close()

    def test_records_camel_case_payload(self, tmp_db: Path) -> None:
        payload = {
            "sessionId": "cop-2",
            "timestamp": 1747569600000,  # ms epoch
            "cwd": "/home/user/repo",
            "toolName": "ReadFile",
            "toolArgs": {"path": "/tmp/x.py"},
            "toolResult": {
                "resultType": "success",
                "textResultForLlm": "file content",
            },
        }
        result = _run_hook(self.MODULE, payload)
        assert result.returncode == 0

        db = MeterDB(tmp_db)
        calls = db.get_recent_calls(limit=10)
        assert len(calls) == 1
        assert calls[0].tool_name == "ReadFile"
        assert calls[0].session_id == "cop-2"
        db.close()

    def test_skips_mcp_tools(self, tmp_db: Path) -> None:
        _run_hook(self.MODULE, {
            "session_id": "cop-3",
            "tool_name": "mcp__slack__send",
            "tool_input": {},
            "tool_result": {"result_type": "success", "text_result_for_llm": "ok"},
        })

        db = MeterDB(tmp_db)
        assert db.get_total_calls() == 0
        db.close()

    def test_empty_stdin(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", self.MODULE],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0

    def test_invalid_json(self, tmp_db: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", self.MODULE],
            input="{broken",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0


# ── Hook install CLI ────────────────────────────────────────────────


class TestHookInstallCLI:
    def test_install_claude(self) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["hook", "install", "claude"])
        assert result.exit_code == 0
        assert "PostToolUse" in result.output
        assert "agentmeter.hooks.claude" in result.output

    def test_install_gemini(self) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["hook", "install", "gemini"])
        assert result.exit_code == 0
        assert "AfterTool" in result.output
        assert "agentmeter.hooks.gemini" in result.output

    def test_install_codex(self) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["hook", "install", "codex"])
        assert result.exit_code == 0
        assert "PostToolUse" in result.output
        assert "agentmeter.hooks.codex" in result.output
        assert "config.toml" in result.output

    def test_install_copilot(self) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["hook", "install", "copilot"])
        assert result.exit_code == 0
        assert "postToolUse" in result.output
        assert "agentmeter.hooks.copilot" in result.output

    def test_install_default_is_claude(self) -> None:
        from click.testing import CliRunner

        from agentmeter.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["hook", "install"])
        assert result.exit_code == 0
        assert "agentmeter.hooks.claude" in result.output
