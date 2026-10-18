"""Tunable resident service settings."""

RESTART_THROTTLE_SECONDS = 10

# Per-command finite limits for native runtime construction and offline checks.
NATIVE_PROBE_TIMEOUT_SECONDS = 30.0
NATIVE_BUILD_TIMEOUT_SECONDS = 600.0

NATIVE_NODE_BUILD_TIMEOUT_SECONDS = 600.0
