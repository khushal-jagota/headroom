"""Control message doubles with WebSocket boundaries, without a network host."""

from __future__ import annotations

import asyncio

from planner.executor.app_transport import ControlWebSocket


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
