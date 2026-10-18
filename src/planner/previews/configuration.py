"""Resource and timeout limits for ephemeral preview traffic."""

from typing import Final

PREVIEW_CHUNK_BYTES: Final = 64 * 1024
MAX_PREVIEW_STREAMS: Final = 32
MAX_PREVIEW_LISTENERS: Final = 32
PREVIEW_OPEN_TIMEOUT_SECONDS: Final = 1.0
PREVIEW_IDLE_TIMEOUT_SECONDS: Final = 30.0
HTTP_CONNECT_TIMEOUT_SECONDS: Final = 1.0
HTTP_IDLE_TIMEOUT_SECONDS: Final = 30.0
