"""Application construction for the translated T3 service. No provider probe."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.usage.UsageService import UsageService
from planner.core.config import Config


def makeUsageService(config: Config, *, now: Callable[[], datetime]) -> UsageService:
    stateDir = Path(config.db_path).resolve().parent / "usage"
    stateDir.mkdir(parents=True, exist_ok=True)
    # These are the same local source roots as T3's home resolvers. This reads no
    # authentication file, starts no CLI, and does not involve a native peer.
    transcriptDirs = [
        ("codex", codex_home().expanduser() / "sessions"),
        (
            "claude",
            Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
            / "projects",
        ),
    ]
    return UsageService(
        stateDir,
        transcriptDirs,
        now=lambda: now().timestamp() * 1000,
    )
