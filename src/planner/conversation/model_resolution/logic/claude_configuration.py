"""Provider-specific document roles and paths, without file I/O."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from planner.conversation.model_resolution.claude_input_contracts import ClaudeJsonValue


class ClaudeConfigurationRole(StrEnum):
    global_state = "global_state"
    settings = "settings"
    managed_settings = "managed_settings"
    remote_policy_cache = "remote_policy_cache"


def claude_global_candidates(environment: Mapping[str, str], home: Path) -> tuple[Path, Path]:
    """The current preferred document and provider's legacy fallback, in order."""
    configured = environment.get("CLAUDE_CONFIG_DIR")
    config = Path(unicodedata.normalize(
        "NFC", configured if configured is not None else str(home / ".claude")
    ))
    suffix = "-custom-oauth" if environment.get("CLAUDE_CODE_CUSTOM_OAUTH_URL") else ""
    legacy_home = Path(configured) if configured else home
    return config / ".config.json", legacy_home / f".claude{suffix}.json"


def claude_has_unsupported_source(document: object, role: ClaudeConfigurationRole) -> bool:
    """Inspect source selectors only where a settings document defines them.

    Global feature caches contain arbitrary member names and opaque values.
    They are not settings documents. Actual policy output is compared by the
    provider snapshot adapter, including policy helper output and remote policy.
    """
    if not isinstance(document, dict):
        return True
    if role is ClaudeConfigurationRole.global_state:
        return False
    # These explicit path selectors have no supported resolver in this observer.
    # Nested model values, environment names and cache members are data, not paths.
    return any(key in document for key in ("include", "import", "config_path", "settings_path"))


def claude_configuration_root(environment: Mapping[str, str], home: Path) -> Path:
    """Preserve the provider's unset/empty distinction before path conversion."""
    configured = environment.get("CLAUDE_CONFIG_DIR")
    return _absolute_root(configured if configured is not None else str(home / ".claude"))


def claude_secure_storage_root(environment: Mapping[str, str], home: Path) -> Path:
    configured = environment.get("CLAUDE_SECURESTORAGE_CONFIG_DIR")
    if configured is None:
        return claude_configuration_root(environment, home)
    return _absolute_root(configured or str(home / ".claude"))


def _absolute_root(text: str) -> Path:
    root = Path(unicodedata.normalize("NFC", text))
    if not root.is_absolute() or "\x00" in text:
        raise ValueError("Unavailable configuration root")
    return root


def parse_claude_document(content: bytes, role: ClaudeConfigurationRole) -> ClaudeJsonValue:
    """Parse the entire strict JSON object into type-sensitive immutable values."""
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Unavailable configuration document")
            result[key] = value
        return result

    def invalid_constant(value: str) -> object:
        raise ValueError("Unavailable configuration document")

    try:
        document = json.loads(
            content.decode("utf-8"), object_pairs_hook=object_pairs,
            parse_constant=invalid_constant,
        )
        if claude_has_unsupported_source(document, role):
            raise ValueError("Unavailable configuration document")
        return _immutable_json(document)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("Unavailable configuration document") from None


def _immutable_json(value: object) -> ClaudeJsonValue:
    if value is None:
        return ClaudeJsonValue("null", None)
    if isinstance(value, bool):
        return ClaudeJsonValue("boolean", value)
    if isinstance(value, int):
        return ClaudeJsonValue("integer", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Unavailable configuration document")
        return ClaudeJsonValue("float", value)
    if isinstance(value, str):
        return ClaudeJsonValue("string", value)
    if isinstance(value, list):
        return ClaudeJsonValue("array", tuple(_immutable_json(item) for item in value))
    if isinstance(value, dict):
        return ClaudeJsonValue("object", tuple(
            (key, _immutable_json(item)) for key, item in sorted(value.items())
        ))
    raise ValueError("Unavailable configuration document")
