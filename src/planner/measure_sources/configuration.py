"""Defaults for ordinary source execution."""

from planner.measure_sources.contracts import RuntimeLimits

DEFAULT_LIMITS = RuntimeLimits(wall_seconds=30.0)
PIPE_CHUNK_BYTES = 8192
POLL_SECONDS = 0.05
