"""An executor archive must never satisfy a home runtime request."""

import pytest

from planner.platform_runtime.selection import select_home_runtime
from planner.platform_runtime.targets import LINUX_HOME, RuntimeTarget


def test_executor_artifact_cannot_select_home_runtime() -> None:
    # Keep OS and architecture equal so only the role distinguishes the payloads.
    executor = RuntimeTarget(LINUX_HOME.system, LINUX_HOME.architecture, "executor")
    with pytest.raises(ValueError, match="does not match"):
        select_home_runtime(executor)
