"""Configuration: every tunable lives here, loaded from config.yaml with PLAN_*
environment overrides. Test-mode flags are environment-only and never in the
checked-in yaml. Nothing in the system reads a timing/limit/path inline."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

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
    dispatch_enabled: bool
    hermes_bin: str
    hermes_profile: str
    worker_skill: str
    # other tunables named across SPEC
    ws_poll_ms: int
    ws_heartbeat_ms: int
    ui_debounce_ms: int
    dispatcher_lock_path: str
    logs_dir: str
    events_read_limit: int
    db_busy_timeout_ms: int
    shutdown_grace_seconds: int
    # adapter selection (registry §9.4)
    gateway_adapter: str
    # optional hosted trusted-ingress boundary
    trusted_ingress_provider: str | None
    trusted_ingress_allowed_login: str | None
    trusted_ingress_canonical_origin: str | None
    # test mode — ENV ONLY, never in config.yaml
    test_mode: bool
    fake_now: str | None
    run_startup_recovery_in_test_mode: bool


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


def _optional_str_value(
    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str
) -> str | None:
    value: object | None = None
    if key in file_cfg:
        value = file_cfg[key]
    if env_var in env:
        value = env[env_var]
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


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


def _is_test_loopback_http_origin(parsed_scheme: str, parsed_netloc: str, test_mode: bool) -> bool:
    if not test_mode or parsed_scheme != "http":
        return False
    host = parsed_netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    return host in {"127.0.0.1", "localhost"}


def _validate_trusted_ingress(
    provider: str | None,
    allowed_login: str | None,
    canonical_origin: str | None,
    *,
    test_mode: bool,
) -> tuple[str | None, str | None, str | None]:
    if provider is not None:
        provider = provider.lower()
    if provider is None and (allowed_login is not None or canonical_origin is not None):
        raise PlannerError(
            ErrorCode.validation,
            "trusted_ingress_provider is required when trusted ingress is configured",
        )
    if provider is None:
        return None, None, None
    if provider != "tailscale":
        raise PlannerError(
            ErrorCode.validation,
            f"unsupported trusted_ingress_provider: {provider}",
        )
    if allowed_login is None:
        raise PlannerError(
            ErrorCode.validation,
            "trusted_ingress_allowed_login is required for Tailscale trusted ingress",
        )
    if canonical_origin is None:
        raise PlannerError(
            ErrorCode.validation,
            "trusted_ingress_canonical_origin is required for trusted ingress",
        )
    parsed = urlsplit(canonical_origin)
    origin_is_https = parsed.scheme == "https"
    origin_is_test_loopback_http = _is_test_loopback_http_origin(
        parsed.scheme, parsed.netloc, test_mode
    )
    if (
        not (origin_is_https or origin_is_test_loopback_http)
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise PlannerError(
            ErrorCode.validation,
            "trusted_ingress_canonical_origin must be an HTTPS origin with no path",
        )
    return provider, allowed_login, f"{parsed.scheme}://{parsed.netloc}"


def load_config(path: str | None = None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    cfg = _read_yaml(path)

    test_mode = _parse_bool(env.get("PLAN_TEST_MODE", "0"), "test_mode")
    fake_now: str | None = env.get("PLAN_FAKE_NOW") or None
    if not test_mode:
        fake_now = None  # §13/item 21: PLAN_FAKE_NOW is ignored when test mode is off
    run_startup_recovery_in_test_mode = test_mode and _parse_bool(
        env.get("PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE", "0"),
        "run_startup_recovery_in_test_mode",
    )

    trusted_ingress_provider = _optional_str_value(
        cfg, env, "trusted_ingress_provider", "PLAN_TRUSTED_INGRESS_PROVIDER"
    )
    trusted_ingress_allowed_login = _optional_str_value(
        cfg, env, "trusted_ingress_allowed_login", "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN"
    )
    trusted_ingress_canonical_origin = _optional_str_value(
        cfg,
        env,
        "trusted_ingress_canonical_origin",
        "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN",
    )
    (
        trusted_ingress_provider,
        trusted_ingress_allowed_login,
        trusted_ingress_canonical_origin,
    ) = _validate_trusted_ingress(
        trusted_ingress_provider,
        trusted_ingress_allowed_login,
        trusted_ingress_canonical_origin,
        test_mode=test_mode,
    )

    return Config(
        db_path=_str_value(cfg, env, "db_path", "PLAN_DB_PATH", "data/planning.db"),
        port=_int_value(cfg, env, "port", "PLAN_PORT", 8767),
        boundary_hour=_int_value(cfg, env, "boundary_hour", "PLAN_BOUNDARY_HOUR", 5),
        tick_seconds=_int_value(cfg, env, "tick_seconds", "PLAN_TICK_SECONDS", 60),
        dispatch_enabled=_bool_value(cfg, env, "dispatch_enabled", "PLAN_DISPATCH_ENABLED", True),
        hermes_bin=_str_value(cfg, env, "hermes_bin", "PLAN_HERMES_BIN", "hermes"),
        hermes_profile=_str_value(cfg, env, "hermes_profile", "PLAN_HERMES_PROFILE", "default"),
        worker_skill=_str_value(cfg, env, "worker_skill", "PLAN_WORKER_SKILL", "panels-worker"),
        ws_poll_ms=_int_value(cfg, env, "ws_poll_ms", "PLAN_WS_POLL_MS", 300),
        ws_heartbeat_ms=_int_value(
            cfg, env, "ws_heartbeat_ms", "PLAN_WS_HEARTBEAT_MS", 15000
        ),
        ui_debounce_ms=_int_value(cfg, env, "ui_debounce_ms", "PLAN_UI_DEBOUNCE_MS", 250),
        dispatcher_lock_path=_str_value(
            cfg, env, "dispatcher_lock_path", "PLAN_DISPATCHER_LOCK_PATH", "data/dispatcher.lock"
        ),
        logs_dir=_str_value(cfg, env, "logs_dir", "PLAN_LOGS_DIR", "data/logs"),
        events_read_limit=_int_value(cfg, env, "events_read_limit", "PLAN_EVENTS_READ_LIMIT", 500),
        db_busy_timeout_ms=_int_value(
            cfg, env, "db_busy_timeout_ms", "PLAN_DB_BUSY_TIMEOUT_MS", 5000
        ),
        shutdown_grace_seconds=_int_value(
            cfg, env, "shutdown_grace_seconds", "PLAN_SHUTDOWN_GRACE_SECONDS", 30
        ),
        gateway_adapter=_str_value(cfg, env, "gateway_adapter", "PLAN_GATEWAY_ADAPTER", "auto"),
        trusted_ingress_provider=trusted_ingress_provider,
        trusted_ingress_allowed_login=trusted_ingress_allowed_login,
        trusted_ingress_canonical_origin=trusted_ingress_canonical_origin,
        test_mode=test_mode,
        fake_now=fake_now,
        run_startup_recovery_in_test_mode=run_startup_recovery_in_test_mode,
    )
