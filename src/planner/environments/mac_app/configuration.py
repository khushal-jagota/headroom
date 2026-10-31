"""Stable native identity and build target."""

import re

BUNDLE_ID = "com.panels.executor.app"
DISPLAY_NAME = "Panels"
ARCHITECTURE = "arm64"
MINIMUM_MACOS = "11.0"


def validate_identity(value: str) -> None:
    """Reject empty or malformed bundle identifiers and LaunchAgent labels."""
    if re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value) is None:
        raise ValueError("identity must contain dot-separated letters, digits, or hyphens")
