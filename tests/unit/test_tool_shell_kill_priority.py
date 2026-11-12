"""Tool shells must be killed before agents, so the startup file must ship and work.

The backends only set BASH_ENV; this proves bash applies the file to the shell and to
what the shell starts, under the plain and login forms both agent CLIs use.
"""

import subprocess
import sys

import pytest

from planner.conversation.tool_shell_kill_priority import (
    TOOL_SHELL_STARTUP,
    with_tool_shell_kill_priority,
)


@pytest.mark.skipif(sys.platform != "linux", reason="the OOM score exists only on Linux")
@pytest.mark.parametrize("flags", ["-c", "-lc"])
def test_tool_shell_and_its_children_take_the_highest_kill_score(flags: str) -> None:
    environment = with_tool_shell_kill_priority({"PATH": "/usr/bin:/bin"})
    assert environment["BASH_ENV"] == str(TOOL_SHELL_STARTUP)
    score = subprocess.run(
        ["bash", flags, "cat /proc/self/oom_score_adj"], env=environment,
        capture_output=True, text=True, check=True,
    )
    assert (score.stdout, score.stderr) == ("1000\n", "")
