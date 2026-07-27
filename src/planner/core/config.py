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
from planner.environments.app import AppValidationError, validate_app_sha

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
    # other tunables named across SPEC
    sse_heartbeat_ms: int
    dispatcher_lock_path: str
    logs_dir: str
    backup_dir: str
    db_busy_timeout_ms: int
    shutdown_grace_seconds: int
    # optional hosted trusted-ingress boundary
    trusted_ingress_provider: str | None
    trusted_ingress_allowed_login: str | None
    trusted_ingress_canonical_origin: str | None
    # test mode — ENV ONLY, never in config.yaml
    test_mode: bool
    fake_now: str | None
    app_sha: str | None = None


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


def _positive_int_value(
    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: int
) -> int:
    """An interval or size that only means something above zero, refused below it."""
    value = _int_value(file_cfg, env, key, env_var, default)
    if value <= 0:
        raise PlannerError(
            ErrorCode.validation, f"{key} must be greater than zero: {value}"
        )
    return value


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

    app_sha = _optional_str_value(cfg, env, "app_sha", "PLAN_APP_SHA")
    if app_sha is not None:
        try:
            validate_app_sha(app_sha)
        except AppValidationError as exc:
            raise PlannerError(ErrorCode.validation, str(exc)) from exc

    return Config(
        db_path=_str_value(cfg, env, "db_path", "PLAN_DB_PATH", "data/planning.db"),
        port=_int_value(cfg, env, "port", "PLAN_PORT", 8767),
        boundary_hour=_int_value(cfg, env, "boundary_hour", "PLAN_BOUNDARY_HOUR", 5),
        tick_seconds=_int_value(cfg, env, "tick_seconds", "PLAN_TICK_SECONDS", 60),
        dispatch_enabled=_bool_value(cfg, env, "dispatch_enabled", "PLAN_DISPATCH_ENABLED", True),
        sse_heartbeat_ms=_positive_int_value(
            cfg, env, "sse_heartbeat_ms", "PLAN_SSE_HEARTBEAT_MS", 15000
        ),
        dispatcher_lock_path=_str_value(
            cfg, env, "dispatcher_lock_path", "PLAN_DISPATCHER_LOCK_PATH", "data/dispatcher.lock"
        ),
        logs_dir=_str_value(cfg, env, "logs_dir", "PLAN_LOGS_DIR", "data/logs"),
        backup_dir=_str_value(cfg, env, "backup_dir", "PLAN_BACKUP_DIR", "data/backups"),
        db_busy_timeout_ms=_int_value(
            cfg, env, "db_busy_timeout_ms", "PLAN_DB_BUSY_TIMEOUT_MS", 5000
        ),
        shutdown_grace_seconds=_int_value(
            cfg, env, "shutdown_grace_seconds", "PLAN_SHUTDOWN_GRACE_SECONDS", 30
        ),
        trusted_ingress_provider=trusted_ingress_provider,
        trusted_ingress_allowed_login=trusted_ingress_allowed_login,
        trusted_ingress_canonical_origin=trusted_ingress_canonical_origin,
        test_mode=test_mode,
        fake_now=fake_now,
        app_sha=app_sha,
    )
