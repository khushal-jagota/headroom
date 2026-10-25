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
from planner.executor.contracts import SshAuthenticationMode


def test_agent_uses_account_home_gui_and_stable_data_with_spaces(tmp_path: Path) -> None:
    home = tmp_path / "Account With Spaces"
    paths = mac_executor_paths(home)
    hosts = tmp_path / "known-hosts"
    hosts.write_text("vps.example ssh-ed25519 fixture-key\n")
    key = tmp_path / "key"
    key.write_text("fixture-key")
    key.chmod(0o600)
    configuration = ServiceConfiguration(
        paths,
        501,
        ExecutorLaunchOptions(
            "vps.example",
            "a" * 40,
            "https://panels.example",
            22,
            "owner",
            key,
            hosts,
            SshAuthenticationMode.public_key,
        ),
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


def test_explicit_ssh_arguments_reach_installed_command(
    tmp_path: Path,
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
    config = ServiceConfiguration(mac_executor_paths(tmp_path), 501, launch=launch)
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
    assert args[0] == str(config.paths.venv / "bin/panels")
    assert args[args.index("--application-revision") + 1] == launch.application_revision
    environment = plistlib.loads(render_launch_agent(config))["EnvironmentVariables"]
    assert environment["PLAN_CLAUDE_EXECUTABLE"] == str(
        config.paths.source
        / ("agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe")
    )
    for invalid in (
        replace(launch, ssh_port=0),
        replace(launch, ssh_port=65536),
        replace(launch, ssh_host="owner@alias"),
        replace(launch, ssh_user=""),
        replace(launch, ssh_identity_file=Path("relative-key")),
    ):
        with pytest.raises(MacExecutorConfigurationError):
            render_launch_agent(replace(config, launch=invalid))
    key.chmod(0o644)
    with pytest.raises(MacExecutorConfigurationError, match="owner-only"):
        render_launch_agent(config)
    key.chmod(0o600)
    hosts.write_text("")
    with pytest.raises(MacExecutorConfigurationError, match="nonempty"):
        render_launch_agent(config)
    hosts.unlink()
    hosts.symlink_to(key)
    with pytest.raises(MacExecutorConfigurationError, match="symlinks"):
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
    config = ServiceConfiguration(mac_executor_paths(tmp_path), 501, launch=launch)
    plist = plistlib.loads(render_launch_agent(config))
    args = plist["ProgramArguments"]
    assert args[args.index("--ssh-auth-mode") + 1] == "tailscale"
    assert plist["EnvironmentVariables"]["TAILSCALE_BE_CLI"] == "1"
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
        with pytest.raises(MacExecutorConfigurationError):
            validate_executor_launch(invalid, check_files=False)
    binary.chmod(0o644)
    with pytest.raises(MacExecutorConfigurationError, match="executable"):
        render_launch_agent(config)
    binary.chmod(0o755)
    link = tmp_path / "tailscale-link"
    link.symlink_to(binary)
    with pytest.raises(MacExecutorConfigurationError, match="symlinks"):
        render_launch_agent(replace(config, launch=replace(launch, tailscale_binary=link)))


def test_explicit_renderer_cli_emits_only_the_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from planner.environments.mac_executor.cli import main

    key, hosts = tmp_path / "key", tmp_path / "hosts"
    key.write_text("fixture-key")
    key.chmod(0o600)
    hosts.write_text("vps.example ssh-ed25519 fixture-host")
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
            "--ssh-host",
            "vps.example",
            "--ssh-port",
            "22",
            "--ssh-user",
            "owner",
            "--ssh-auth-mode",
            "public_key",
            "--ssh-identity-file",
            str(key),
            "--ssh-known-hosts",
            str(hosts),
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
