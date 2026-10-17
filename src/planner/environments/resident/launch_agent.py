"""Render a GUI-session LaunchAgent and installation commands, without execution."""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path

from planner.environments.resident.configuration import RESTART_THROTTLE_SECONDS
from planner.environments.resident.contracts import ResidentError, ServiceConfiguration
from planner.environments.resident.paths import data_paths


def render_launch_agent(configuration: ServiceConfiguration) -> bytes:
    if configuration.user_id < 0 or configuration.label != "com.panels.executor":
        raise ResidentError("invalid resident service identity")
    paths = configuration.paths
    if any(not path.is_absolute() for path in (paths.home, paths.app, paths.executor_root)):
        raise ResidentError("resident service paths must be absolute")
    data = data_paths(paths)
    arguments = [str(paths.app / "bin/panels"), "executor", "run"]
    backend_path = ":".join(str(path) for path in (
        paths.home / ".local/bin", paths.home / ".npm-global/bin",
        Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin"),
    ))
    if configuration.launch is not None:
        launch = configuration.launch
        if launch.backend_path is not None:
            entries = launch.backend_path.split(os.pathsep)
            if any(not entry or not Path(entry).is_absolute() for entry in entries):
                raise ResidentError("backend PATH requires absolute directories")
            backend_path = launch.backend_path
        if (
            not launch.ssh_host
            or launch.ssh_host.startswith("-")
            or any(char.isspace() for char in launch.ssh_host)
            or re.fullmatch(r"[0-9a-f]{40}", launch.application_revision) is None
            or not launch.panels_server_url.startswith(("http://", "https://"))
            or not launch.remote_command.strip()
        ):
            raise ResidentError("invalid executor launch configuration")
        arguments += [
            "--ssh-host", launch.ssh_host,
            "--panels-server-url", launch.panels_server_url,
            "--remote-command", launch.remote_command,
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
