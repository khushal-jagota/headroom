"""Build scrubbed subprocess environments for isolated Panels runs."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from planner.environments.contracts import ResolvedEnvironmentInstance
from planner.environments.logic.credentials import validate_environment_values

_AMBIENT_ALLOWLIST = frozenset({"LANG", "LANGUAGE", "PATH", "TERM", "TMPDIR"})
_TEST_FAKE_NOW = "2026-07-04T12:00:00+00:00"
_TEST_TIMING_ENV = {
    "PLAN_WS_POLL_MS": "50",
    "PLAN_WS_HEARTBEAT_MS": "500",
    "PLAN_UI_DEBOUNCE_MS": "50",
    "PLAN_TICK_SECONDS": "1",
}


def build_environment_run_env(
    instance: ResolvedEnvironmentInstance,
    *,
    credentials: Mapping[str, str],
    ambient: Mapping[str, str],
    hermes_python: Path,
    runtime_port: int,
) -> dict[str, str]:
    run_env = _allowed_ambient_env(ambient)
    run_env.update(validate_environment_values(credentials, kind=instance.kind))
    run_env.update(
        _contract_owned_env(
            instance,
            hermes_python=hermes_python,
            runtime_port=runtime_port,
        )
    )
    return run_env


def build_test_environment_run_env(
    instance: ResolvedEnvironmentInstance,
    *,
    credentials: Mapping[str, str],
    ambient: Mapping[str, str],
    hermes_python: Path,
    runtime_port: int,
) -> dict[str, str]:
    run_env = build_environment_run_env(
        instance,
        credentials=credentials,
        ambient=ambient,
        hermes_python=hermes_python,
        runtime_port=runtime_port,
    )
    run_env.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": _TEST_FAKE_NOW,
            **_TEST_TIMING_ENV,
        }
    )
    return run_env


def _allowed_ambient_env(ambient: Mapping[str, str]) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for key, value in ambient.items():
        if key in _AMBIENT_ALLOWLIST or key.startswith("LC_"):
            allowed[key] = value
    return allowed


def _contract_owned_env(
    instance: ResolvedEnvironmentInstance,
    *,
    hermes_python: Path,
    runtime_port: int,
) -> dict[str, str]:
    return {
        "PLAN_DB_PATH": str(instance.db_path),
        "PLAN_PORT": str(runtime_port),
        "PLAN_LOGS_DIR": str(instance.logs_dir),
        "PLAN_DISPATCHER_LOCK_PATH": str(instance.dispatcher_lock_path),
        "PLAN_SERVER_CONTROL_SOCKET": str(instance.server_control_socket_path),
        "PLAN_HERMES_HOME": str(instance.hermes_home),
        "PLAN_HERMES_PYTHON": str(hermes_python),
        "HOME": str(instance.hermes_home),
    }
