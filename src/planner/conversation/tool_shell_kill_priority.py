"""Agent tools are the first thing Linux kills when the machine runs out of memory.

An agent's tool shell, and everything that shell starts, raises its own OOM score
to the maximum. The agent is not a shell and keeps its own score, and the live
Panels service asks for the lowest one, so a test or browser goes before either.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOL_SHELL_STARTUP = Path(__file__).with_name("tool_shell_startup.bash")


def with_tool_shell_kill_priority(environment: dict[str, str]) -> dict[str, str]:
    """Point bash at the startup file on Linux, where the OOM score exists."""
    if sys.platform == "linux":
        environment["BASH_ENV"] = str(TOOL_SHELL_STARTUP)
    return environment
