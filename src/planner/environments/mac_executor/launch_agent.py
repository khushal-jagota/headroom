"""Render a GUI-session LaunchAgent without installation or service actions."""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from planner.environments.mac_executor.configuration import RESTART_THROTTLE_SECONDS
from planner.environments.mac_executor.contracts import (
    ExecutorLaunchOptions,
    MacExecutorConfigurationError,
    ServiceConfiguration,
)


def validate_executor_launch(launch: ExecutorLaunchOptions) -> None:
    """Validate the explicit HTTPS origin and logical executor identity offline."""
    try:
        origin = urlsplit(launch.panels_server_url)
        port = origin.port
    except ValueError as error:
        raise MacExecutorConfigurationError("invalid executor HTTPS origin") from error
    if (
        re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", launch.executor_id) is None
        or not launch.application_revision.strip()
        or origin.scheme != "https"
        or not origin.hostname
        or origin.username is not None
        or origin.password is not None
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or (port is not None and not 1 <= port <= 65535)
        or any(char.isspace() or ord(char) < 32 for char in launch.panels_server_url)
    ):
        raise MacExecutorConfigurationError("invalid explicit executor HTTPS launch configuration")


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
    validate_executor_launch(launch)
    arguments += [
        "--application-revision",
        launch.application_revision,
        "--executor-id",
        launch.executor_id,
        "--panels-server-url",
        launch.panels_server_url,
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
