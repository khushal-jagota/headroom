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
        str(home / "Applications/Panels.app/Contents/MacOS/Panels"),
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
    assert plist["AssociatedBundleIdentifiers"] == ["com.panels.executor.app"]
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


def test_production_layout_keeps_account_home_and_separates_job_resources(tmp_path: Path) -> None:
    from planner.executor.configuration import ExecutorPaths

    root = tmp_path / "Production State"
    configuration = replace(
        _VALID_CONFIGURATION,
        home=tmp_path,
        source_path=tmp_path / "Production/source",
        venv_path=tmp_path / "Production/venv",
        app_path=tmp_path / "Applications/Panels Production.app",
        executor_root=root,
        job_label="com.panels.executor.production",
        bundle_id="com.panels.executor.production.app",
    )
    plist = plistlib.loads(render_launch_agent(configuration))
    assert plist["Label"] == "com.panels.executor.production"
    assert plist["AssociatedBundleIdentifiers"] == ["com.panels.executor.production.app"]
    assert plist["ProgramArguments"][0] == str(
        tmp_path / "Applications/Panels Production.app/Contents/MacOS/Panels"
    )
    env = plist["EnvironmentVariables"]
    assert env["HOME"] == str(tmp_path)
    assert env["PATH"].split(":")[0] == str(tmp_path / "Production/venv/bin")
    assert env["PLAN_CLAUDE_EXECUTABLE"] == str(
        tmp_path
        / "Production/source/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    )
    assert env["PLAN_EXECUTOR_ROOT"] == str(root)
    paths = ExecutorPaths(root)
    assert paths.ipc_socket.is_relative_to(root)
    assert plist["StandardOutPath"] == str(root / "logs/launch-agent.stdout.log")
    assert plist["StandardErrorPath"] == str(root / "logs/launch-agent.stderr.log")


@pytest.mark.parametrize(
    "changes",
    [
        {"source_path": Path("relative")},
        {"venv_path": Path("relative")},
        {"venv_path": Path("/ambiguous:venv")},
        {"app_path": Path("relative.app")},
        {"app_path": Path("/not-a-bundle")},
        {"bundle_id": "bad identity"},
        {"job_label": "bad/label"},
    ],
)
def test_agent_rejects_invalid_installation_inputs(changes: dict[str, object]) -> None:
    with pytest.raises(MacExecutorConfigurationError):
        render_launch_agent(replace(_VALID_CONFIGURATION, **changes))  # type: ignore[arg-type]
