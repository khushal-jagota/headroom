"""Render a GUI-session LaunchAgent and installation commands, without execution."""

from __future__ import annotations

import plistlib
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
    return plistlib.dumps(
        {
            "Label": configuration.label,
            "ProgramArguments": [str(paths.app / "bin/panels"), "executor", "run"],
            "LimitLoadToSessionType": "Aqua",
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": RESTART_THROTTLE_SECONDS,
            "EnvironmentVariables": {
                "HOME": str(paths.home),
                "PLAN_EXECUTOR_ROOT": str(data.root),
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
