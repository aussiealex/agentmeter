"""Platform-aware paths and helpers.

Centralises OS-specific logic so the rest of the codebase stays clean.
"""

from __future__ import annotations

import sys
from pathlib import Path


def data_dir() -> Path:
    """Return the platform-appropriate data directory for AgentMeter.

    - Linux:   ~/.local/share/agentmeter
    - macOS:   ~/Library/Application Support/agentmeter
    - Windows: %APPDATA%/agentmeter  (e.g. C:/Users/<user>/AppData/Roaming)
    """
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / "agentmeter"


def project_name(path: str) -> str:
    """Extract project name from a directory path.

    Works with both forward slashes (POSIX) and backslashes (Windows).
    Returns the final component: "/home/user/MyProject" → "MyProject"
    """
    if not path:
        return ""
    return Path(path).name
