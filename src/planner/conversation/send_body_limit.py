"""Bound conversation send bodies before request parsing materializes their JSON."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Final

# Base64 expands the approved 10 MiB file and 3 MiB image budgets to about 17.34 MiB.
# The remainder carries JSON structure, file metadata, and ordinary message text.
MAX_CONVERSATION_SEND_REQUEST_BYTES: Final = 20 * 1024 * 1024

_SEND_PATHS: Final = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/conversation/conversations/[^/]+/send$",
        r"^/api/chief/conversation/send$",
        r"^/api/tickets/[^/]+/conversation/send$",
        r"^/api/items/[^/]+/conversation/send$",
        r"^/api/messages/send$",
    )
)

_Receive = Callable[[], Awaitable[dict[str, Any]]]
_Send = Callable[[dict[str, Any]], Awaitable[None]]


class _ConversationSendBodyTooLarge(Exception):
    pass


class ConversationSendBodyLimitMiddleware:
    """Reject an oversized conversation send before FastAPI buffers or validates it."""

    def __init__(self, app: Any, max_bytes: int = MAX_CONVERSATION_SEND_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self, scope: dict[str, Any], receive: _Receive, send: _Send
    ) -> None:
        if not _is_conversation_send(scope):
            await self.app(scope, receive, send)
            return
        if _content_length_exceeds(scope, self.max_bytes):
            await _reject(send)
            return

        byte_count = 0

        async def receive_with_limit() -> dict[str, Any]:
            nonlocal byte_count
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    byte_count += len(body)
                    if byte_count > self.max_bytes:
                        raise _ConversationSendBodyTooLarge
            return message

        try:
            await self.app(scope, receive_with_limit, send)
        except _ConversationSendBodyTooLarge:
            await _reject(send)


def _is_conversation_send(scope: dict[str, Any]) -> bool:
    if scope.get("type") != "http" or str(scope.get("method", "")).upper() != "POST":
        return False
    path = scope.get("path")
    return isinstance(path, str) and any(pattern.fullmatch(path) for pattern in _SEND_PATHS)


def _content_length_exceeds(scope: dict[str, Any], max_bytes: int) -> bool:
    for name, value in scope.get("headers", ()):
        if name.lower() != b"content-length":
            continue
        try:
            if int(value) > max_bytes:
                return True
        except ValueError:
            continue
    return False


async def _reject(send: _Send) -> None:
    body = json.dumps(
        {"detail": "a conversation send request is too large"}, separators=(",", ":")
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
