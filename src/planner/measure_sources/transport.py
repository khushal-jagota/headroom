"""Ordinary HTTP requests used by trusted stored scripts."""

from __future__ import annotations

from typing import Any

import httpx


def request(
    url: str,
    *,
    method: str = "GET",
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> httpx.Response:
    # Standard DNS, TLS, proxy, compression, and HTTP behavior. No automatic retries.
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        return client.request(method, url, json=json_body, headers=headers)
