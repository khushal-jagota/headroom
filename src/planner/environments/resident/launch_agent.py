"""Render a GUI-session LaunchAgent and installation commands, without execution."""

from __future__ import annotations

import os
import plistlib
import re
import stat
from pathlib import Path

from planner.environments.resident.configuration import RESTART_THROTTLE_SECONDS
from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    ResidentError,
    ServiceConfiguration,
)
from planner.environments.resident.paths import data_paths


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
        raise ResidentError("invalid explicit executor SSH launch configuration")
    for path, secret in ((launch.ssh_identity_file, True), (launch.ssh_known_hosts_file, False)):
        if not path.is_absolute() or any(ord(c) < 32 for c in str(path)):
            raise ResidentError("executor SSH files must be absolute paths")
        if not check_files:
            continue
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ResidentError("executor SSH files must contain no symlinks")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ResidentError("executor SSH files must be owner-owned regular files")
        if secret and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ResidentError("executor SSH identity must be owner-only")
        if not path.read_bytes().strip():
            raise ResidentError("executor SSH files must be nonempty")


def render_launch_agent(configuration: ServiceConfiguration) -> bytes:
    if configuration.user_id < 0 or configuration.label != "com.panels.executor":
        raise ResidentError("invalid resident service identity")
    paths = configuration.paths
    if any(not path.is_absolute() for path in (paths.home, paths.app, paths.executor_root)):
        raise ResidentError("resident service paths must be absolute")
    data = data_paths(paths)
    arguments = [str(paths.app / "bin/panels"), "executor", "run"]
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
    if configuration.launch is not None:
        launch = configuration.launch
        if launch.backend_path is not None:
            entries = launch.backend_path.split(os.pathsep)
            if any(not entry or not Path(entry).is_absolute() for entry in entries):
                raise ResidentError("backend PATH requires absolute directories")
            backend_path = launch.backend_path
        validate_executor_launch(launch, check_files=True)
        arguments += [
            "--ssh-host",
            launch.ssh_host,
            "--ssh-port",
            str(launch.ssh_port),
            "--ssh-user",
            launch.ssh_user,
            "--ssh-identity-file",
            str(launch.ssh_identity_file),
            "--ssh-known-hosts",
            str(launch.ssh_known_hosts_file),
            "--panels-server-url",
            launch.panels_server_url,
            "--remote-command",
            launch.remote_command,
        ]
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
                "PLAN_EXECUTOR_ROOT": str(data.root),
                "PATH": str(paths.app / "bin") + os.pathsep + backend_path,
            },
            "StandardOutPath": str(data.logs / "launch-agent.stdout.log"),
            "StandardErrorPath": str(data.logs / "launch-agent.stderr.log"),
        },
        sort_keys=True,
    )


def launchctl_install_commands(
    configuration: ServiceConfiguration,
    plist: Path,
) -> tuple[tuple[str, ...], ...]:
    """Reviewable commands only; gui attachment requires an existing login session.

    There is deliberately no bootout/kill operation here: an update stop needs
    the executor adapter's atomic process-identity fence, which launchctl alone
    cannot supply. No system domain or sudo fallback is generated.
    """
    render_launch_agent(configuration)
    if not plist.is_absolute():
        raise ResidentError("LaunchAgent plist path must be absolute")
    domain = f"gui/{configuration.user_id}"
    return (
        ("launchctl", "bootstrap", domain, str(plist)),
        ("launchctl", "kickstart", f"{domain}/{configuration.label}"),
    )
