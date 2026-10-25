"""Render a GUI-session LaunchAgent without installation or service actions."""

from __future__ import annotations

import os
import plistlib
import re
import stat
from pathlib import Path

from planner.environments.mac_executor.configuration import RESTART_THROTTLE_SECONDS
from planner.environments.mac_executor.contracts import (
    ExecutorLaunchOptions,
    MacExecutorConfigurationError,
    ServiceConfiguration,
)
from planner.executor.contracts import SshAuthenticationMode, SshConfigurationError
from planner.executor.logic.ssh_configuration import validate_authentication_inputs


def validate_executor_launch(launch: ExecutorLaunchOptions, *, check_files: bool) -> None:
    """Validate explicit executor inputs without ambient SSH configuration or contact."""
    if (
        re.fullmatch(r"[A-Za-z0-9_.:-]+", launch.ssh_host) is None
        or launch.ssh_host.startswith("-")
        or not 1 <= launch.ssh_port <= 65535
        or re.fullmatch(r"[A-Za-z0-9_.-]+", launch.ssh_user) is None
        or launch.ssh_user.startswith("-")
        or re.fullmatch(r"[0-9a-f]{40}", launch.application_revision) is None
        or not launch.panels_server_url.startswith(("http://", "https://"))
        or not launch.remote_command.strip()
    ):
        raise MacExecutorConfigurationError("invalid explicit executor SSH launch configuration")
    try:
        validate_authentication_inputs(
            launch.ssh_auth_mode, launch.ssh_identity_file, launch.tailscale_binary, launch.ssh_port
        )
    except SshConfigurationError as exc:
        raise MacExecutorConfigurationError(str(exc)) from exc
    if launch.ssh_auth_mode is SshAuthenticationMode.tailscale:
        binary = launch.tailscale_binary
        if binary is None or not binary.is_absolute() or any(ord(c) < 32 for c in str(binary)):
            raise MacExecutorConfigurationError("tailscale requires an absolute executable path")
        if check_files:
            if any(p.is_symlink() for p in (binary, *binary.parents)):
                raise MacExecutorConfigurationError(
                    "Tailscale binary path must contain no symlinks"
                )
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise MacExecutorConfigurationError("Tailscale binary must be an executable file")
    files = [(launch.ssh_known_hosts_file, False)]
    if launch.ssh_identity_file is not None:
        files.append((launch.ssh_identity_file, True))
    for path, secret in files:
        if not path.is_absolute() or any(ord(c) < 32 for c in str(path)):
            raise MacExecutorConfigurationError("executor SSH files must be absolute paths")
        if not check_files:
            continue
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise MacExecutorConfigurationError("executor SSH files must contain no symlinks")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise MacExecutorConfigurationError(
                "executor SSH files must be owner-owned regular files"
            )
        if secret and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise MacExecutorConfigurationError("executor SSH identity must be owner-only")
        if not path.read_bytes().strip():
            raise MacExecutorConfigurationError("executor SSH files must be nonempty")


def render_launch_agent(configuration: ServiceConfiguration) -> bytes:
    if configuration.user_id < 0 or configuration.label != "com.panels.executor":
        raise MacExecutorConfigurationError("invalid Mac executor service identity")
    paths = configuration.paths
    if any(
        not path.is_absolute()
        for path in (paths.home, paths.source, paths.venv, paths.executor_root)
    ):
        raise MacExecutorConfigurationError("Mac executor service paths must be absolute")
    arguments = [str(paths.venv / "bin/panels"), "executor", "run"]
    backend_path = ":".join(
        str(path)
        for path in (
            paths.home / ".local/bin",
            paths.home / ".npm-global/bin",
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/bin"),
        )
    )
    launch = configuration.launch
    if launch.backend_path is not None:
        entries = launch.backend_path.split(os.pathsep)
        if any(not entry or not Path(entry).is_absolute() for entry in entries):
            raise MacExecutorConfigurationError("backend PATH requires absolute directories")
        backend_path = launch.backend_path
    validate_executor_launch(launch, check_files=True)
    arguments += [
        "--application-revision",
        launch.application_revision,
        "--ssh-host",
        launch.ssh_host,
        "--ssh-port",
        str(launch.ssh_port),
        "--ssh-user",
        launch.ssh_user,
        "--ssh-auth-mode",
        launch.ssh_auth_mode,
        "--ssh-known-hosts",
        str(launch.ssh_known_hosts_file),
        "--panels-server-url",
        launch.panels_server_url,
        "--remote-command",
        launch.remote_command,
    ]
    if launch.ssh_identity_file is not None:
        arguments += ["--ssh-identity-file", str(launch.ssh_identity_file)]
    if launch.tailscale_binary is not None:
        arguments += ["--tailscale-binary", str(launch.tailscale_binary)]
    return plistlib.dumps(
        {
            "Label": configuration.label,
            "ProgramArguments": arguments,
            "LimitLoadToSessionType": "Aqua",
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": RESTART_THROTTLE_SECONDS,
            "EnvironmentVariables": {
                "HOME": str(paths.home),
                "PLAN_CLAUDE_EXECUTABLE": str(
                    paths.source
                    / ("agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe")
                ),
                "PLAN_EXECUTOR_ROOT": str(paths.executor_root),
                "PATH": str(paths.venv / "bin") + os.pathsep + backend_path,
            },
            "StandardOutPath": str(paths.executor_root / "logs" / "launch-agent.stdout.log"),
            "StandardErrorPath": str(paths.executor_root / "logs" / "launch-agent.stderr.log"),
        },
        sort_keys=True,
    )
