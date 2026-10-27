"""Credential bounds and private key storage policy."""

MAX_NAME_LENGTH = 200
MAX_ACCOUNT_LENGTH = 200
MAX_SECRET_LENGTH = 16_384
MAX_ORIGINS = 32
MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 25
KEY_BYTES = 32
NONCE_BYTES = 12

MAX_REQUEST_BYTES = 64 * 1024
MAX_ORIGIN_LENGTH = 2048
MAX_HEADER_NAME_LENGTH = 128
RESERVED_AUTH_HEADER_NAMES = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "accept-encoding",
        "content-type",
        "accept",
        "proxy-authorization",
        "proxy-connection",
        "upgrade",
        "te",
        "trailer",
    }
)
