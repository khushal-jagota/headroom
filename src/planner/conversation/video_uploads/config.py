"""Video intake limits, isolated for tuning."""

MAX_VIDEO_BYTES = 250 * 1024 * 1024
VIDEO_BUDGET_BYTES = 10 * 1024 * 1024 * 1024
FREE_SPACE_FLOOR_BYTES = 5 * 1024 * 1024 * 1024
UPLOAD_TTL_SECONDS = 24 * 60 * 60
TRANSFER_CHUNK_BYTES = 256 * 1024
# Chunks are written with no transaction open. Progress, the cancel check, and the
# disk reservation check are saved in one short transaction per this many bytes.
PROGRESS_RECORD_BYTES = 8 * 1024 * 1024
PROBE_TIMEOUT_SECONDS = 10
PROBE_OUTPUT_BYTES = 64 * 1024
CLEANUP_INTERVAL_SECONDS = 60
CONTAINER_HEADER_BYTES = 4096
