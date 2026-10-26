"""Print a LaunchAgent plist from explicit inputs; perform no lifecycle actions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from planner.environments.mac_executor.contracts import (
    ExecutorLaunchOptions,
    MacExecutorConfigurationError,
    ServiceConfiguration,
)
from planner.environments.mac_executor.launch_agent import render_launch_agent
from planner.environments.mac_executor.paths import mac_executor_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--executor-root", type=Path)
    parser.add_argument("--application-revision", required=True)
    parser.add_argument("--executor-id", required=True)
    parser.add_argument("--panels-server-url", required=True)
    parser.add_argument("--backend-path", required=True)
    args = parser.parse_args()
    if not args.home.is_absolute():
        parser.error("--home must be an absolute path")
    configuration = ServiceConfiguration(
        paths=mac_executor_paths(args.home, args.executor_root),
        user_id=args.user_id,
        launch=ExecutorLaunchOptions(
            executor_id=args.executor_id,
            application_revision=args.application_revision,
            panels_server_url=args.panels_server_url,
            backend_path=args.backend_path,
        ),
    )
    try:
        output = render_launch_agent(configuration)
    except (MacExecutorConfigurationError, OSError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
