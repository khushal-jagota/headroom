"""The one bounded external price source used by T3 Code. No provider credentials."""

from __future__ import annotations

import json
from time import monotonic
from typing import cast

import httpx

LITELLM_RATES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
RATES_TTL_SECONDS = 24 * 60 * 60
RATES_REFRESH_FLOOR_SECONDS = 60
RATES_TIMEOUT_SECONDS = 10
MAX_RATES_BYTES = 16 * 1024 * 1024


def fetch_rate_document() -> dict[str, object]:
    """Bound time and decoded bytes before a complete external document is owned."""
    started = monotonic()
    content = bytearray()
    with httpx.Client(timeout=RATES_TIMEOUT_SECONDS, follow_redirects=False) as client:
        with client.stream("GET", LITELLM_RATES_URL) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if monotonic() - started >= RATES_TIMEOUT_SECONDS:
                    raise TimeoutError("The model price read reached its time limit.")
                if len(content) + len(chunk) > MAX_RATES_BYTES:
                    raise ValueError("The model price document exceeds its size limit.")
                content.extend(chunk)
    document: object = json.loads(content)
    if not isinstance(document, dict):
        raise ValueError("The model price document is not an object.")
    return cast(dict[str, object], document)
