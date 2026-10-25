import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    ResidentError,
    ServiceConfiguration,
)
from planner.environments.resident.launch_agent import (
    launchctl_install_commands,
    render_launch_agent,
    validate_executor_launch,
)
from planner.environments.resident.paths import resident_paths
from planner.executor.contracts import SshAuthenticationMode


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
        "WatchPaths",
        "QueueDirectories",
        "StartInterval",
        "StartCalendarInterval",
    }
    assert not automatic_triggers & set(plist)
    assert plist["LimitLoadToSessionType"] == "Aqua"
    commands = launchctl_install_commands(
        configuration, home / "Library/LaunchAgents/com.panels.executor.plist"
    )
    assert commands[0][:3] == ("launchctl", "bootstrap", "gui/501")
    assert commands[1] == ("launchctl", "kickstart", "gui/501/com.panels.executor")
    assert all("system" not in command and "sudo" not in command for command in commands)


def test_explicit_ssh_arguments_reach_installed_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key, hosts = tmp_path / "private key", tmp_path / "known hosts"
    key.write_text("fixture-private-key")
    key.chmod(0o600)
    hosts.write_text("vps.example ssh-ed25519 fixture-pinned-key\n")
    launch = ExecutorLaunchOptions(
        "vps.example",
        "a" * 40,
        "https://panels.example",
        2222,
        "owner",
        key,
        hosts,
        SshAuthenticationMode.public_key,
    )
    config = ServiceConfiguration(resident_paths(tmp_path), 501, launch=launch)
    args = plistlib.loads(render_launch_agent(config))["ProgramArguments"]
    for flag, value in {
        "--ssh-auth-mode": "public_key",
        "--ssh-host": "vps.example",
        "--ssh-port": "2222",
        "--ssh-user": "owner",
        "--ssh-identity-file": str(key),
        "--ssh-known-hosts": str(hosts),
    }.items():
        assert args[args.index(flag) + 1] == value
    # app/bin/panels invokes resident_main: only this wrapper supplies the revision.
    import sys

    from planner.executor import entrypoint

    assert args[0] == str(config.paths.app / "bin/panels")
    assert "--application-revision" not in args
    observed: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", args.copy())
    monkeypatch.setattr(entrypoint, "main", lambda: observed.append(sys.argv.copy()))
    entrypoint.resident_main(launch.application_revision)
    assert len(observed) == 1
    assert observed[0].count("--application-revision") == 1
    assert observed[0][-2:] == ["--application-revision", launch.application_revision]
    for invalid in (
        replace(launch, ssh_port=0),
        replace(launch, ssh_port=65536),
        replace(launch, ssh_host="owner@alias"),
        replace(launch, ssh_user=""),
        replace(launch, ssh_identity_file=Path("relative-key")),
    ):
        with pytest.raises(ResidentError):
            render_launch_agent(replace(config, launch=invalid))
    key.chmod(0o644)
    with pytest.raises(ResidentError, match="owner-only"):
        render_launch_agent(config)
    key.chmod(0o600)
    hosts.write_text("")
    with pytest.raises(ResidentError, match="nonempty"):
        render_launch_agent(config)
    hosts.unlink()
    hosts.symlink_to(key)
    with pytest.raises(ResidentError, match="symlinks"):
        render_launch_agent(config)


def test_tailscale_launch_suppresses_key_and_requires_safe_executable(tmp_path: Path) -> None:
    hosts, binary = tmp_path / "hosts", tmp_path / "tailscale"
    hosts.write_text("vps.example ssh-ed25519 fixture-pin\n")
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    launch = ExecutorLaunchOptions(
        "vps.example",
        "a" * 40,
        "https://panels.example",
        22,
        "owner",
        None,
        hosts,
        SshAuthenticationMode.tailscale,
        tailscale_binary=binary,
    )
    config = ServiceConfiguration(resident_paths(tmp_path), 501, launch=launch)
    args = plistlib.loads(render_launch_agent(config))["ProgramArguments"]
    assert args[args.index("--ssh-auth-mode") + 1] == "tailscale"
    assert args[args.index("--tailscale-binary") + 1] == str(binary)
    assert "--ssh-identity-file" not in args
    for invalid in (
        replace(launch, ssh_identity_file=tmp_path / "unused-key"),
        replace(launch, ssh_port=2222),
        replace(launch, tailscale_binary=None),
        replace(launch, tailscale_binary=Path("relative")),
        replace(launch, tailscale_binary=Path("/unsafe\npath")),
        replace(launch, ssh_auth_mode=SshAuthenticationMode.public_key),
    ):
        with pytest.raises(ResidentError):
            validate_executor_launch(invalid, check_files=False)
    binary.chmod(0o644)
    with pytest.raises(ResidentError, match="executable"):
        render_launch_agent(config)
    binary.chmod(0o755)
    link = tmp_path / "tailscale-link"
    link.symlink_to(binary)
    with pytest.raises(ResidentError, match="symlinks"):
        render_launch_agent(replace(config, launch=replace(launch, tailscale_binary=link)))
