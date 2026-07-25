"""Stable launcher boundary for a validated production release."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from planner.environments.release import validate_release_manifest

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
        "PLAN_HERMES_HOME",
        "PLAN_HERMES_PYTHON",
        "PLAN_SERVER_CONTROL_SOCKET",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
    }
)


def build_release_launch_env(release_root: Path, *, ambient: Mapping[str, str]) -> dict[str, str]:
    root = release_root.expanduser().resolve()
    manifest = validate_release_manifest(root / "manifest.json", require_runtime=True)
    allowed = {
        key: value
        for key, value in ambient.items()
        if key in EXTERNAL_RUNTIME_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    allowed.update({"PLAN_RELEASE_SHA": manifest.release_sha, "PLAN_RELEASE_ROOT": str(root)})
    return allowed


def launch_release(
    release_root: Path, argv: list[str], *, ambient: Mapping[str, str] | None = None
) -> None:
    env = build_release_launch_env(release_root, ambient=os.environ if ambient is None else ambient)
    os.execve(str(release_root / ".venv" / "bin" / "python"), argv, env)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: release_launcher RELEASE_ROOT [planner arguments]")
    root = Path(sys.argv[1]).expanduser().resolve()
    launch_release(
        root,
        [str(root / ".venv" / "bin" / "python"), "-m", "planner", *sys.argv[2:]],
    )


if __name__ == "__main__":
    main()
