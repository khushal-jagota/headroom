import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.mac_executor.contracts import (
    ExecutorLaunchOptions,
    MacExecutorConfigurationError,
    ServiceConfiguration,
)
from planner.environments.mac_executor.launch_agent import (
    render_launch_agent,
    validate_executor_launch,
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


def test_https_launch_passes_identity_and_preserves_provider_access(tmp_path: Path) -> None:
    launch = ExecutorLaunchOptions("owner-mac", "a" * 40, "https://panels.example:9443")
    config = ServiceConfiguration(mac_executor_paths(tmp_path), 501, launch=launch)
    plist = plistlib.loads(render_launch_agent(config))
    args = plist["ProgramArguments"]
    assert args[args.index("--executor-id") + 1] == "owner-mac"
    assert args[args.index("--panels-server-url") + 1] == launch.panels_server_url
    assert not any(arg.startswith("--ssh-") for arg in args)
    assert plist["EnvironmentVariables"]["PLAN_CLAUDE_EXECUTABLE"] == str(
        config.paths.source / "agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    )
    for invalid in (
        replace(launch, executor_id=""),
        replace(launch, executor_id="bad/route"),
        replace(launch, panels_server_url="http://panels.example"),
        replace(launch, panels_server_url="https://owner@panels.example"),
        replace(launch, panels_server_url="https://panels.example/api"),
    ):
        with pytest.raises(MacExecutorConfigurationError):
            validate_executor_launch(invalid, check_files=False)


def test_explicit_renderer_cli_emits_only_the_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from planner.environments.mac_executor.cli import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render",
            "--home",
            str(tmp_path),
            "--user-id",
            "501",
            "--application-revision",
            "a" * 40,
            "--executor-id",
            "owner-mac",
            "--panels-server-url",
            "https://panels.example",
            "--backend-path",
            "/usr/bin:/bin",
        ],
    )
    main()
    output = capsys.readouterr()
    assert not output.err
    plist = plistlib.loads(output.out.encode())
    assert plist["ProgramArguments"][:3] == [
        str(tmp_path / "Deployments/PanelsExecutor/venv/bin/panels"),
        "executor",
        "run",
    ]
    assert plist["EnvironmentVariables"]["PATH"] == (
        str(tmp_path / "Deployments/PanelsExecutor/venv/bin") + ":/usr/bin:/bin"
    )
