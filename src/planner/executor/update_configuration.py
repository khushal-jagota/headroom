"""Mac update and supervisor inputs, also used by the fixed bootstrap.

A protocol-22 Mac receives its update target in every lease reply, so it no longer
polls the update route; an older Mac still does, once a second.
"""

MAC_UPDATE_SCHEMA_VERSION = 1
MAC_RUNTIME_UPDATE_EXIT_CODE = 75
MAC_RUNTIME_CHILD_ENV = "PLAN_EXECUTOR_RUNTIME_CHILD"
MAC_RUNTIME_READY_ENV = "PLAN_EXECUTOR_RUNTIME_READY"
# How often the fixed supervisor checks for a new runtime's ready marker, and how often
# a runtime replaying a planned handoff re-checks whether its old output is receipted.
MAC_UPDATE_POLL_SECONDS = 1.0
MAC_RUNTIME_READY_TIMEOUT_SECONDS = 60.0
MAC_UPDATE_TERMINATE_SECONDS = 10.0
MAX_MAC_RUNTIME_ARCHIVE_BYTES = 2 * 1024**3
MAX_MAC_RUNTIME_EXPANDED_BYTES = 4 * 1024**3
MAX_MAC_UPDATE_RESPONSE_BYTES = 64 * 1024
MAX_MAC_RUNTIME_MANIFEST_BYTES = 16 * 1024
MAX_MAC_UPDATE_DETAIL_CHARACTERS = 500
MAC_RUNTIME_ARCHIVE_NAME = "panels-mac-runtime.tar.gz"
MAC_RUNTIME_MANIFEST_NAME = "mac-runtime.json"
MAC_RUNTIME_HANDOFF_ENV = "PLAN_EXECUTOR_RUNTIME_HANDOFF"
