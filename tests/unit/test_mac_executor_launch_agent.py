import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.mac_executor import (
    MacExecutorConfigurationError,
    ServiceConfiguration,
    render_launch_agent,
)


def test_agent_uses_account_home_gui_and_stable_data_with_spaces(tmp_path: Path) -> None:
    home = tmp_path / "Account With Spaces"
    configuration = ServiceConfiguration(
        home,
        "owner-mac",
        "a" * 40,
        "https://panels.example",
    )
    plist = plistlib.loads(render_launch_agent(configuration))
    assert plist["ProgramArguments"][:3] == [
        str(configuration.venv / "bin/panels"),
        "executor",
        "run",
    ]
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert "TAILSCALE_BE_CLI" not in plist["EnvironmentVariables"]
    assert plist["EnvironmentVariables"]["PLAN_EXECUTOR_ROOT"] == str(
        home / "Library/Application Support/Panels/executor"
    )
    assert plist["EnvironmentVariables"]["PATH"].split(":")[0] == str(configuration.venv / "bin")
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    automatic_triggers = {
        "WatchPaths",
        "QueueDirectories",
        "StartInterval",
        "StartCalendarInterval",
    }
    assert not automatic_triggers & set(plist)
    assert plist["LimitLoadToSessionType"] == "Aqua"

    assert plist["Label"] == "com.panels.executor"
    assert plist["RunAtLoad"] is True
    assert plist["ThrottleInterval"] == 10
    assert plist["ProgramArguments"][3:] == [
        "--application-revision",
        "a" * 40,
        "--executor-id",
        "owner-mac",
        "--panels-server-url",
        "https://panels.example",
    ]


def test_agent_preserves_explicit_root_backend_path_and_origin(tmp_path: Path) -> None:
    root = tmp_path / "Existing Data"
    configuration = ServiceConfiguration(
        tmp_path,
        "owner-mac",
        "diagnostic-label",
        "https://panels.example:8443/",
        executor_root=root,
        backend_path="/custom tools/bin:/usr/bin",
    )
    plist = plistlib.loads(render_launch_agent(configuration))
    assert plist["EnvironmentVariables"]["PLAN_EXECUTOR_ROOT"] == str(root)
    assert plist["EnvironmentVariables"]["PATH"] == (
        str(configuration.venv / "bin") + ":/custom tools/bin:/usr/bin"
    )
    assert plist["ProgramArguments"][-1] == "https://panels.example:8443/"
    assert plist["StandardOutPath"] == str(root / "logs/launch-agent.stdout.log")
    assert plist["StandardErrorPath"] == str(root / "logs/launch-agent.stderr.log")


@pytest.mark.parametrize(
    "origin",
    [
        "http://panels.example",
        "https://user:secret@panels.example",
        "https://panels.example/path",
        "https://panels.example?query=1",
        "https://panels.example#fragment",
        "https://panels.example:0",
        "https://panels.example:65536",
        "https://[invalid",
        "https://panels.example/\n",
    ],
)
def test_agent_refuses_invalid_server_origin(tmp_path: Path, origin: str) -> None:
    configuration = ServiceConfiguration(tmp_path, "owner-mac", "revision", origin)
    with pytest.raises(MacExecutorConfigurationError):
        render_launch_agent(configuration)


_VALID_CONFIGURATION = ServiceConfiguration(
    Path("/owner"), "owner-mac", "revision", "https://panels.example"
)


@pytest.mark.parametrize(
    "configuration",
    [
        replace(_VALID_CONFIGURATION, executor_id="bad id"),
        replace(_VALID_CONFIGURATION, application_revision=" "),
        replace(_VALID_CONFIGURATION, backend_path=""),
        replace(_VALID_CONFIGURATION, backend_path="relative/bin"),
        replace(_VALID_CONFIGURATION, executor_root=Path("relative")),
        replace(_VALID_CONFIGURATION, home=Path("relative")),
    ],
)
def test_agent_refuses_invalid_explicit_inputs(configuration: ServiceConfiguration) -> None:
    with pytest.raises(MacExecutorConfigurationError):
        render_launch_agent(configuration)
