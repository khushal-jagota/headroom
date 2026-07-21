"""Credential/environment-file parsing for isolated Panels environments."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from planner.environments.contracts import (
    DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
    EnvironmentCredentialPolicy,
    EnvironmentKind,
    EnvironmentValidationError,
)

_ENVIRONMENT_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "HOME",
        "LOGNAME",
        "PATH",
        "PWD",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHELL",
        "USER",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
    }
)
_FORBIDDEN_PREFIXES = ("PLAN_", "HERMES_")


def parse_environment_file(
    path: Path,
    *,
    kind: EnvironmentKind,
    policy: EnvironmentCredentialPolicy = DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = _parse_assignment(line, line_number)
        if key in values:
            raise EnvironmentValidationError(f"duplicate environment key: {key}")
        validate_environment_key(key, kind=kind, policy=policy)
        values[key] = _strip_matching_quotes(value)
    return values


def validate_environment_values(
    values: Mapping[str, str],
    *,
    kind: EnvironmentKind,
    policy: EnvironmentCredentialPolicy = DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for key, value in values.items():
        validate_environment_key(key, kind=kind, policy=policy)
        validated[key] = str(value)
    return validated


def validate_environment_key(
    key: str,
    *,
    kind: EnvironmentKind,
    policy: EnvironmentCredentialPolicy = DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
) -> None:
    if _ENVIRONMENT_KEY_RE.fullmatch(key) is None:
        raise EnvironmentValidationError(f"malformed environment key: {key}")
    if _is_forbidden_key(key):
        raise EnvironmentValidationError(f"forbidden environment key: {key}")
    if key not in policy.allowed_keys:
        raise EnvironmentValidationError(f"unknown environment key: {key}")
    if kind != "live" and key in policy.production_only_keys:
        raise EnvironmentValidationError(f"production-only environment key: {key}")


def _parse_assignment(line: str, line_number: int) -> tuple[str, str]:
    if "=" not in line:
        raise EnvironmentValidationError(f"malformed environment line {line_number}")
    key, value = line.split("=", 1)
    key = key.strip()
    if not key or _ENVIRONMENT_KEY_RE.fullmatch(key) is None:
        raise EnvironmentValidationError(f"malformed environment line {line_number}")
    return key, value.strip()


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_forbidden_key(key: str) -> bool:
    return key in _FORBIDDEN_EXACT_KEYS or key.startswith(_FORBIDDEN_PREFIXES)
