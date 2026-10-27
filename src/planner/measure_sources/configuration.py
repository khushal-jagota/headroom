"""Host-owned bounds. Source records and environment variables cannot widen them."""

from planner.measure_sources.contracts import RuntimeLimits

DEFAULT_LIMITS = RuntimeLimits(
    wall_seconds=30.0,
    script_cpu_seconds=2,
    script_memory_bytes=256 * 1024 * 1024,
    script_file_descriptors=32,
    script_file_bytes=0,
    broker_cpu_seconds=10,
    broker_memory_bytes=512 * 1024 * 1024,
    broker_file_descriptors=64,
    request_count=64,
    request_frame_bytes=64 * 1024,
    response_bytes=2 * 1024 * 1024,
    total_response_bytes=16 * 1024 * 1024,
    child_output_bytes=256 * 1024,
    result_bytes=256 * 1024,
    observation_count=10_000,
    json_depth=32,
)
BROKER_CONFIGURATION_BYTES = 1024 * 1024
SCRIPT_CONTEXT_BYTES = 256 * 1024
SCRIPT_BYTES = 256 * 1024
PIPE_CHUNK_BYTES = 8192
POLL_SECONDS = 0.02
REAP_SECONDS = 1.0

# These are the only host runtime resources mounted into the source namespace.
SANDBOX_RUNTIME_ROOTS = (
    "/usr/bin/python3.13",
    "/usr/lib/python3.13",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib64/ld-linux-x86-64.so.2",
    "/usr/share/zoneinfo",
)
