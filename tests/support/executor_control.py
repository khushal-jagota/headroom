"""Control message doubles with WebSocket boundaries, without a network host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import patch

from planner.execution_identity import ExecutorIdentity
from planner.executor.link import ControlMessageStream, Link, WebSocketStream
from planner.executor.mac_outbox import MacOutbox
from planner.executor.protocol import (
    QueuedEnvelope,
    WireMessage,
    encode_message,
)


class MessageStream(WebSocketStream):
    def __init__(self) -> None:
        super().__init__()
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent: list[bytes] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def receive_bytes(self) -> bytes:
        return await self.incoming.get()

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def aclose(self) -> None:
        self._closed = True
        self.feed_eof()

    def feed_data(self, data: bytes) -> None:
        self.incoming.put_nowait(data)

    def feed_eof(self) -> None:
        self.feed_data(b"")


@asynccontextmanager
async def paused_outbox(outbox: MacOutbox) -> AsyncIterator[None]:
    """Hold item transport, without holding a database or connection lifecycle lock."""
    release = asyncio.Event()
    send = outbox._send_one

    async def held(link: Link, envelope: QueuedEnvelope) -> None:
        await release.wait()
        await send(link, envelope)

    with patch.object(outbox, "_send_one", held):
        try:
            yield
        finally:
            release.set()


def message_link(stream: ControlMessageStream, identity: ExecutorIdentity) -> Link:
    """Use the real matching and generation logic over the existing message double."""
    return Link(identity, stream)


async def deliver(link: Link, message: WireMessage) -> None:
    cast(MessageStream, link._stream).feed_data(encode_message(message))
    await asyncio.sleep(0)
