"""Stable launcher boundary for a validated production app."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from planner.environments.app import validate_app_manifest
from planner.environments.executor_launch_contracts import VpsExecutorLaunchConfiguration
from planner.environments.vps_launch import read_vps_launch, validate_vps_launch_for_application

EXTERNAL_RUNTIME_ENVIRONMENT_KEYS = frozenset(
    {
        "HOME",
        "LOGNAME",
        "PATH",
        "SHELL",
        "LANG",
        "TERM",
        "TMPDIR",
        "USER",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "PLAN_CONFIG_PATH",
        "PLAN_DB_PATH",
        "PLAN_PORT",
        "PLAN_BOUNDARY_HOUR",
        "PLAN_TICK_SECONDS",
        "PLAN_DISPATCH_ENABLED",
        "PLAN_SSE_HEARTBEAT_MS",
        "PLAN_DISPATCHER_LOCK_PATH",
        "PLAN_LOGS_DIR",
        "PLAN_BACKUP_DIR",
        "PLAN_DB_BUSY_TIMEOUT_MS",
        "PLAN_SHUTDOWN_GRACE_SECONDS",
        "PLAN_TRUSTED_INGRESS_PROVIDER",
        "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN",
        "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN",
        "PLAN_GROQ_API_KEY",
        "PLAN_HERMES_HOME",
        "PLAN_HERMES_PYTHON",
        "PLAN_CLAUDE_EXECUTABLE",
        "PLAN_SERVER_CONTROL_SOCKET",
        "PLAN_UPDATE_REPOSITORY",
        "PLAN_UPDATE_TOKEN_FILE",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
    }
)


def build_app_launch_env(
    app_root: Path,
    *,
    ambient: Mapping[str, str],
    executor_launch: VpsExecutorLaunchConfiguration | None = None,
) -> dict[str, str]:
    root = app_root.expanduser().resolve()
    manifest = validate_app_manifest(root / "manifest.json", require_runtime=True)
    allowed = {
        key: value
        for key, value in ambient.items()
        if key in EXTERNAL_RUNTIME_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    allowed.update({"PLAN_APP_SHA": manifest.app_sha, "PLAN_APP_ROOT": str(root)})
    if executor_launch is not None:
        validate_vps_launch_for_application(executor_launch)
        allowed["PLAN_EXECUTOR_MODE"] = executor_launch.mode
        if executor_launch.executor_id is not None:
            allowed["PLAN_EXECUTOR_ID"] = executor_launch.executor_id
    return allowed


def launch_app(
    app_root: Path,
    argv: list[str],
    *,
    ambient: Mapping[str, str] | None = None,
    executor_launch: VpsExecutorLaunchConfiguration | None = None,
) -> None:
    env = build_app_launch_env(
        app_root,
        ambient=os.environ if ambient is None else ambient,
        executor_launch=executor_launch,
    )
    os.execve(str(app_root / ".venv" / "bin" / "python"), argv, env)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: app_launcher APP_ROOT [planner arguments]")
    root = Path(sys.argv[1]).expanduser().resolve()
    configuration = read_vps_launch(root.parent / "data/executor-launch.json")
    launch_app(
        root,
        [str(root / ".venv" / "bin" / "python"), "-m", "planner", *sys.argv[2:]],
        executor_launch=configuration or VpsExecutorLaunchConfiguration("local"),
    )


if __name__ == "__main__":
    main()
