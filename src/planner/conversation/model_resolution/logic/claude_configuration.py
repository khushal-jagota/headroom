"""Provider-specific document roles and paths, without file I/O."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path


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
