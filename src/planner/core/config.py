"""Configuration: every tunable lives here, loaded from config.yaml with PLAN_*
environment overrides. Test-mode flags are environment-only and never in the
checked-in yaml. Nothing in the system reads a timing/limit/path inline."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from planner.core.errors import ErrorCode, PlannerError

HOST: Final = "127.0.0.1"                    # §2: the bind is fixed, not tunable
DEFAULT_CONFIG_PATH: Final = "config.yaml"

_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class Config:
    # §13 named keys
    db_path: str
    port: int
    boundary_hour: int
    tick_seconds: int
    claim_ttl_seconds: int
    max_runs: int
    failure_limit: int
    dispatch_enabled: bool
    hermes_bin: str
    hermes_profile: str
    worker_skill: str
    # other tunables named across SPEC
    ws_poll_ms: int
    ui_debounce_ms: int
    run_max_seconds: int
    boundary_timeout_seconds: int
    dispatcher_lock_path: str
    logs_dir: str
    events_read_limit: int
    db_busy_timeout_ms: int
    # adapter selection (registry §9.4)
    spawn_adapter: str
    boundary_adapter: str
    gateway_adapter: str
    # test mode — ENV ONLY, never in config.yaml
    test_mode: bool
    fake_now: str | None


def _parse_bool(raw: object, key: str) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise PlannerError(ErrorCode.validation, f"invalid boolean for {key}: {raw!r}")


def _parse_int(raw: object, key: str) -> int:
    if isinstance(raw, bool):
        raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}") from exc
    raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}")


def _read_yaml(path: str | None) -> dict[str, object]:
    resolved = Path(path) if path is not None else Path(DEFAULT_CONFIG_PATH)
    if not resolved.exists():
        return {}
    loaded = yaml.safe_load(resolved.read_text())
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise PlannerError(ErrorCode.validation, f"config root must be a mapping: {resolved}")
    return loaded


def _str_value(
    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: str
) -> str:
    value: object = default
    if key in file_cfg:
        value = file_cfg[key]
    if env_var in env:
        value = env[env_var]
    return str(value)


def _int_value(
    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: int
) -> int:
    value: object = default
    if key in file_cfg:
        value = file_cfg[key]
    if env_var in env:
        value = env[env_var]
    return _parse_int(value, key)


def _bool_value(
    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: bool
) -> bool:
    value: object = default
    if key in file_cfg:
        value = file_cfg[key]
    if env_var in env:
        value = env[env_var]
    return _parse_bool(value, key)


def load_config(path: str | None = None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    cfg = _read_yaml(path)

    test_mode = _parse_bool(env.get("PLAN_TEST_MODE", "0"), "test_mode")
    fake_now: str | None = env.get("PLAN_FAKE_NOW") or None
    if not test_mode:
        fake_now = None  # §13/item 21: PLAN_FAKE_NOW is ignored when test mode is off

    return Config(
        db_path=_str_value(cfg, env, "db_path", "PLAN_DB_PATH", "data/planning.db"),
        port=_int_value(cfg, env, "port", "PLAN_PORT", 8767),
        boundary_hour=_int_value(cfg, env, "boundary_hour", "PLAN_BOUNDARY_HOUR", 5),
        tick_seconds=_int_value(cfg, env, "tick_seconds", "PLAN_TICK_SECONDS", 60),
        claim_ttl_seconds=_int_value(cfg, env, "claim_ttl_seconds", "PLAN_CLAIM_TTL_SECONDS", 900),
        max_runs=_int_value(cfg, env, "max_runs", "PLAN_MAX_RUNS", 2),
        failure_limit=_int_value(cfg, env, "failure_limit", "PLAN_FAILURE_LIMIT", 2),
        dispatch_enabled=_bool_value(cfg, env, "dispatch_enabled", "PLAN_DISPATCH_ENABLED", True),
        hermes_bin=_str_value(cfg, env, "hermes_bin", "PLAN_HERMES_BIN", "hermes"),
        hermes_profile=_str_value(cfg, env, "hermes_profile", "PLAN_HERMES_PROFILE", "default"),
        worker_skill=_str_value(cfg, env, "worker_skill", "PLAN_WORKER_SKILL", "planning-worker"),
        ws_poll_ms=_int_value(cfg, env, "ws_poll_ms", "PLAN_WS_POLL_MS", 300),
        ui_debounce_ms=_int_value(cfg, env, "ui_debounce_ms", "PLAN_UI_DEBOUNCE_MS", 250),
        run_max_seconds=_int_value(cfg, env, "run_max_seconds", "PLAN_RUN_MAX_SECONDS", 1800),
        boundary_timeout_seconds=_int_value(
            cfg, env, "boundary_timeout_seconds", "PLAN_BOUNDARY_TIMEOUT_SECONDS", 60
        ),
        dispatcher_lock_path=_str_value(
            cfg, env, "dispatcher_lock_path", "PLAN_DISPATCHER_LOCK_PATH", "data/dispatcher.lock"
        ),
        logs_dir=_str_value(cfg, env, "logs_dir", "PLAN_LOGS_DIR", "data/logs"),
        events_read_limit=_int_value(cfg, env, "events_read_limit", "PLAN_EVENTS_READ_LIMIT", 500),
        db_busy_timeout_ms=_int_value(
            cfg, env, "db_busy_timeout_ms", "PLAN_DB_BUSY_TIMEOUT_MS", 5000
        ),
        spawn_adapter=_str_value(cfg, env, "spawn_adapter", "PLAN_SPAWN_ADAPTER", "auto"),
        boundary_adapter=_str_value(cfg, env, "boundary_adapter", "PLAN_BOUNDARY_ADAPTER", "auto"),
        gateway_adapter=_str_value(cfg, env, "gateway_adapter", "PLAN_GATEWAY_ADAPTER", "auto"),
        test_mode=test_mode,
        fake_now=fake_now,
    )


def read_dispatch_enabled(path: str | None = None, env: Mapping[str, str] | None = None) -> bool:
    """§7.1 fail-safe: the dispatcher re-reads this every tick. Any read/parse
    failure returns False (a missing config file is not a failure — defaults apply)."""
    try:
        env = os.environ if env is None else env
        return _bool_value(_read_yaml(path), env, "dispatch_enabled", "PLAN_DISPATCH_ENABLED", True)
    except Exception:
        return False
