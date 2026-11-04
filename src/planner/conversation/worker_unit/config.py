"""Resource policy for the Linux Worker process boundary."""

WORKER_MEMORY_MAX = "6G"
WORKER_TEMP_ROOT = "~"
WORKER_SLICE = "panels-workers.slice"
SLICE_MEMORY_MAX_BYTES = 24 * 1024**3
SLICE_CPU_WEIGHT_MAX = 10
CONTROL_TIMEOUT_SECONDS = 10
OOM_REASON = "Worker stopped because it exceeded its memory limit."
