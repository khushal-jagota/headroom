import plistlib
from pathlib import Path

from planner.environments.resident.contracts import ServiceConfiguration
from planner.environments.resident.launch_agent import (
    launchctl_install_commands,
    render_launch_agent,
)
from planner.environments.resident.paths import resident_paths


def test_agent_uses_account_home_gui_and_stable_data_with_spaces(tmp_path: Path) -> None:
    home = tmp_path / "Account With Spaces"
    paths = resident_paths(home)
    configuration = ServiceConfiguration(paths, 501)
    plist = plistlib.loads(render_launch_agent(configuration))
    assert plist["ProgramArguments"] == [str(paths.app / "bin/panels"), "executor", "run"]
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert plist["EnvironmentVariables"]["PLAN_EXECUTOR_ROOT"] == str(
        home / "Library/Application Support/Panels/executor"
    )
    assert plist["EnvironmentVariables"]["PATH"].split(":")[0] == str(paths.app / "bin")
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    automatic_triggers = {
        "WatchPaths", "QueueDirectories", "StartInterval", "StartCalendarInterval"
    }
    assert not automatic_triggers & set(plist)
    assert plist["LimitLoadToSessionType"] == "Aqua"
    commands = launchctl_install_commands(
        configuration, home / "Library/LaunchAgents/com.panels.executor.plist"
    )
    assert commands[0][:3] == ("launchctl", "bootstrap", "gui/501")
    assert commands[1] == ("launchctl", "kickstart", "gui/501/com.panels.executor")
    assert all("system" not in command and "sudo" not in command for command in commands)
