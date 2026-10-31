"""Print a GUI-session LaunchAgent plist; perform no installation or service actions."""

from __future__ import annotations

import argparse
import os
import plistlib
import sys
from dataclasses import dataclass
from pathlib import Path

from planner.environments.mac_app.configuration import BUNDLE_ID, validate_identity
from planner.executor.configuration import (
    LAUNCH_AGENT_RESTART_THROTTLE_SECONDS,
    validate_executor_launch,
)


class MacExecutorConfigurationError(RuntimeError):
    """The explicit LaunchAgent inputs are invalid."""


@dataclass(frozen=True)
class ServiceConfiguration:
    home: Path
    executor_id: str
    application_revision: str
    panels_server_url: str
    executor_root: Path | None = None
    backend_path: str | None = None

    source_path: Path | None = None
    venv_path: Path | None = None
    app_path: Path | None = None
    job_label: str = "com.panels.executor"
    bundle_id: str = BUNDLE_ID

    @property
    def source(self) -> Path:
        return self.source_path or self.home / "Deployments/PanelsExecutor/source"

    @property
    def venv(self) -> Path:
        return self.venv_path or self.home / "Deployments/PanelsExecutor/venv"


def render_launch_agent(configuration: ServiceConfiguration) -> bytes:
    executor_root = (
        configuration.executor_root
        or configuration.home / "Library/Application Support/Panels/executor"
    )
    app = configuration.app_path or configuration.home / "Applications/Panels.app"
    paths = (configuration.home, executor_root, configuration.source, configuration.venv, app)
    if any(not path.is_absolute() or "\0" in str(path) for path in paths):
        raise MacExecutorConfigurationError("Mac executor service paths must be absolute")
    if configuration.venv_path is not None and os.pathsep in str(configuration.venv_path):
        raise MacExecutorConfigurationError("environment path cannot contain the PATH separator")
    if app.suffix != ".app":
        raise MacExecutorConfigurationError("app path must name an .app bundle")
    try:
        validate_identity(configuration.job_label)
        validate_identity(configuration.bundle_id)
        validate_executor_launch(
            configuration.executor_id,
            configuration.application_revision,
            configuration.panels_server_url,
        )
    except ValueError as error:
        raise MacExecutorConfigurationError(str(error)) from error
    backend_path = configuration.backend_path
    if backend_path is None:
        backend_path = os.pathsep.join(
            str(path)
            for path in (
                configuration.home / ".local/bin",
                configuration.home / ".npm-global/bin",
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            )
        )
    if any(not entry or not Path(entry).is_absolute() for entry in backend_path.split(os.pathsep)):
        raise MacExecutorConfigurationError("backend PATH requires absolute directories")
    return plistlib.dumps(
        {
            "Label": configuration.job_label,
            "AssociatedBundleIdentifiers": [configuration.bundle_id],
            "ProgramArguments": [
                str(app / "Contents/MacOS/Panels"),
                "executor",
                "run",
                "--application-revision",
                configuration.application_revision,
                "--executor-id",
                configuration.executor_id,
                "--panels-server-url",
                configuration.panels_server_url,
            ],
            "LimitLoadToSessionType": "Aqua",
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": LAUNCH_AGENT_RESTART_THROTTLE_SECONDS,
            "EnvironmentVariables": {
                "HOME": str(configuration.home),
                "PLAN_CLAUDE_EXECUTABLE": str(
                    configuration.source
                    / "agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
                ),
                "PLAN_EXECUTOR_ROOT": str(executor_root),
                "PATH": str(configuration.venv / "bin") + os.pathsep + backend_path,
            },
            "StandardOutPath": str(executor_root / "logs/launch-agent.stdout.log"),
            "StandardErrorPath": str(executor_root / "logs/launch-agent.stderr.log"),
        },
        sort_keys=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--source", dest="source_path", type=Path)
    parser.add_argument("--venv", dest="venv_path", type=Path)
    parser.add_argument("--app", dest="app_path", type=Path)
    parser.add_argument("--job-label", default="com.panels.executor")
    parser.add_argument("--bundle-id", default=BUNDLE_ID)
    parser.add_argument("--executor-root", type=Path)
    parser.add_argument("--application-revision", required=True)
    parser.add_argument("--executor-id", required=True)
    parser.add_argument("--panels-server-url", required=True)
    parser.add_argument("--backend-path", required=True)
    args = parser.parse_args()
    try:
        output = render_launch_agent(ServiceConfiguration(**vars(args)))
    except (MacExecutorConfigurationError, OSError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
