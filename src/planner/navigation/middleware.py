"""Capture accepted command commits without changing domain responses or receipts."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from planner.navigation import proof
from planner.navigation.command_contracts import (
    COVERAGE_HEADER,
    FENCE_HEADER,
)
from planner.navigation.commands import is_navigation_command
from planner.navigation.proof_limits import MAX_CAPTURE_WITNESSES, MAX_FENCE_BYTES

_log = logging.getLogger(__name__)


class NavigationCommandFenceMiddleware:
    """Pure ASGI: endpoint execution keeps its task identity and response bytes."""

    def __init__(
        self, app: ASGIApp, *, conn_factory: Callable[[], sqlite3.Connection], db_path: str
    ) -> None:
        self.app = app
        self.conn_factory = conn_factory
        self.db_path = db_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_navigation_command(
            scope["method"],
            scope["path"],
        ):
            await self.app(scope, receive, send)
            return
        capture = proof.begin_capture(self.db_path, max_witnesses=MAX_CAPTURE_WITNESSES)
        detached = False

        async def with_fence(message: Message) -> None:
            nonlocal detached
            if message["type"] == "http.response.start":
                # The endpoint has finished its accepted work; later background tasks
                # and response cleanup cannot enter this command's evidence.
                proof.end_capture(capture)
                detached = True
                if not 200 <= message["status"] < 300:
                    await send(message)
                    return
                headers = list(message.get("headers", []))
                try:
                    conn = self.conn_factory()
                    try:
                        fence = proof.response_fence(conn, capture)
                    finally:
                        conn.close()
                    encoded = json.dumps(fence, separators=(",", ":"), ensure_ascii=True).encode(
                        "ascii"
                    )
                    if len(encoded) <= MAX_FENCE_BYTES:
                        headers.append((FENCE_HEADER, encoded))
                        headers.append((COVERAGE_HEADER, fence["coverage"].encode("ascii")))
                    else:
                        headers.append((COVERAGE_HEADER, b"unknown"))
                except Exception:
                    # This runs after durable domain success. Evidence failure cannot
                    # transform it into an error that might encourage a duplicate send.
                    _log.exception("navigation command fence unavailable")
                    headers.append((COVERAGE_HEADER, b"unknown"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, with_fence)
        finally:
            if not detached:
                proof.end_capture(capture)
