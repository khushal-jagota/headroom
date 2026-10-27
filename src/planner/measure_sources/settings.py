"""Application limits for source management and bounded status reads."""

MAX_SCRIPT_BYTES = 64 * 1024
MAX_DESTINATIONS = 64
DEFAULT_PAGE_LIMIT = 30
MAX_PAGE_LIMIT = 100
LATEST_VALUES_PREVIEW = 5
MAX_RUN_RESULT_BYTES = 256 * 1024
# A slot admits throughout its minute and collection has a thirty-second deadline.
STATUS_SLOT_GRACE_SECONDS = 90
MAX_CONCURRENT_RUNS = 2
