import plistlib
from pathlib import Path

from planner.environments.mac_executor.contracts import (
    ExecutorLaunchOptions,
    ServiceConfiguration,
)
from planner.environments.mac_executor.launch_agent import (
    render_launch_agent,
)
from planner.environments.mac_executor.paths import mac_executor_paths


def test_agent_uses_account_home_gui_and_stable_data_with_spaces(tmp_path: Path) -> None:
    home = tmp_path / "Account With Spaces"
    paths = mac_executor_paths(home)
    configuration = ServiceConfiguration(
        paths,
        501,
        ExecutorLaunchOptions("owner-mac", "a" * 40, "https://panels.example"),
    )
    plist = plistlib.loads(render_launch_agent(configuration))
    assert plist["ProgramArguments"][:3] == [str(paths.venv / "bin/panels"), "executor", "run"]
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert "TAILSCALE_BE_CLI" not in plist["EnvironmentVariables"]
    assert plist["EnvironmentVariables"]["PLAN_EXECUTOR_ROOT"] == str(
        home / "Library/Application Support/Panels/executor"
    )
    assert plist["EnvironmentVariables"]["PATH"].split(":")[0] == str(paths.venv / "bin")
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    automatic_triggers = {
        "WatchPaths",
        "QueueDirectories",
        "StartInterval",
        "StartCalendarInterval",
    }
    assert not automatic_triggers & set(plist)
    assert plist["LimitLoadToSessionType"] == "Aqua"
