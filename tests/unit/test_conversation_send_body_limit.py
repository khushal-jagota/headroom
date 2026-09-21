from __future__ import annotations

import asyncio
from typing import Any

from planner.conversation.send_body_limit import ConversationSendBodyLimitMiddleware

_SEND_PATH = "/api/conversation/conversations/c/send"


def test_content_length_rejects_every_send_before_body_or_downstream_read() -> None:
    downstream_called = False
    receive_called = False
    sent: list[dict[str, Any]] = []

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, Any]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = ConversationSendBodyLimitMiddleware(downstream, max_bytes=5)
    asyncio.run(
        middleware(
            _scope(headers=[(b"content-length", b"6")]),
            receive,
            send,
        )
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 413


def test_chunked_body_stops_before_the_over_limit_chunk_reaches_downstream() -> None:
    chunks = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    downstream_chunks: list[bytes] = []
    sent: list[dict[str, Any]] = []

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            downstream_chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                return

    async def receive() -> dict[str, Any]:
        return next(chunks)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = ConversationSendBodyLimitMiddleware(downstream, max_bytes=5)
    asyncio.run(middleware(_scope(), receive, send))

    assert downstream_chunks == [b"123"]
    assert sent[0]["status"] == 413


def _scope(
    *, path: str = _SEND_PATH, headers: list[tuple[bytes, bytes]] | None = None
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers or [],
    }
