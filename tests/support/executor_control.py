"""Control message doubles with WebSocket boundaries, without a network host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import patch

from planner.execution_identity import ExecutorIdentity
from planner.executor.link import ControlWebSocket, Link
from planner.executor.mac_outbox import MacOutbox
from planner.executor.protocol import (
    ControlMessageWriter,
    OutboxLink,
    QueuedEnvelope,
    WireMessage,
    encode_message,
)


class BinaryPeer:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent: list[bytes] = []
        self.closed = False

    async def receive_bytes(self, max_bytes: int) -> bytes:
        return await self.incoming.get()

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def aclose(self) -> None:
        self.closed = True
        self.incoming.put_nowait(b"")


class MessageReader(ControlWebSocket):
    def __init__(self) -> None:
        self.binary_peer = BinaryPeer()
        super().__init__(self.binary_peer)

    def feed_data(self, data: bytes) -> None:
        self.binary_peer.incoming.put_nowait(data)

    def feed_eof(self) -> None:
        self.feed_data(b"")


@asynccontextmanager
async def paused_outbox(outbox: MacOutbox) -> AsyncIterator[None]:
    """Hold item transport, without holding a database or connection lifecycle lock."""
    release = asyncio.Event()
    send = outbox._send_one

    async def held(link: OutboxLink, envelope: QueuedEnvelope) -> None:
        await release.wait()
        await send(link, envelope)

    with patch.object(outbox, "_send_one", held):
        try:
            yield
        finally:
            release.set()


def message_link(writer: object, identity: ExecutorIdentity) -> Link:
    """Use the real matching and generation logic over the existing message double."""
    return Link(identity, MessageReader(), cast(ControlMessageWriter, writer))


async def deliver(link: Link, message: WireMessage) -> None:
    cast(MessageReader, link._reader).feed_data(encode_message(message))
    await asyncio.sleep(0)
