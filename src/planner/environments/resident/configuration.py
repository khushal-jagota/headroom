"""Tunable resident service settings."""

RESTART_THROTTLE_SECONDS = 10

# Per-command finite limits for native runtime construction and offline checks.
NATIVE_PROBE_TIMEOUT_SECONDS = 30.0
NATIVE_BUILD_TIMEOUT_SECONDS = 600.0

NATIVE_NODE_BUILD_TIMEOUT_SECONDS = 600.0

# Standard package inputs and the explicitly qualified Mac target.
APPLICATION_DISTRIBUTION = "planner"
APPLICATION_ENTRYPOINT = "planner.executor.entrypoint"
APPLICATION_CONSOLE_SCRIPT = "panels"
MAC_PYTHON_VERSION = "3.14.3"
BUILD_REQUIREMENTS = "requirements-build.txt"
EXECUTOR_REQUIREMENTS = "requirements-executor.txt"
INSTALLED_IDENTITY = "resident-installed.json"

# Bound retained command output and teardown, including inherited descendant pipes.
NATIVE_CLEANUP_GRACE_SECONDS = 2.0
NATIVE_STDOUT_LIMIT_BYTES = 16 * 1024 * 1024
NATIVE_STDERR_LIMIT_BYTES = 64 * 1024
