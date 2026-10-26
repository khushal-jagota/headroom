"""Generation-bound preview leases over separate Mac-dialed WebSockets."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

from planner.executor.contracts import (
    ByteReader,
    ByteWriter,
    JsonObject,
    PreviewPeer,
    WireMessage,
    WireMessageKind,
)
from planner.previews.configuration import (
    MAX_PREVIEW_LISTENERS,
    MAX_PREVIEW_STREAMS,
    PREVIEW_CHUNK_BYTES,
    PREVIEW_IDLE_TIMEOUT_SECONDS,
    PREVIEW_OPEN_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


def _valid_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


async def copy_streams(reader: ByteReader, writer: ByteWriter) -> None:
    while data := await asyncio.wait_for(
        reader.read(PREVIEW_CHUNK_BYTES), PREVIEW_IDLE_TIMEOUT_SECONDS
    ):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), PREVIEW_IDLE_TIMEOUT_SECONDS)


async def _bridge_peer(reader: ByteReader, writer: ByteWriter, peer: PreviewPeer) -> None:
    async def to_peer() -> None:
        while data := await asyncio.wait_for(
            reader.read(PREVIEW_CHUNK_BYTES), PREVIEW_IDLE_TIMEOUT_SECONDS
        ):
            await asyncio.wait_for(peer.send_bytes(data), PREVIEW_IDLE_TIMEOUT_SECONDS)

    async def from_peer() -> None:
        while data := await asyncio.wait_for(
            peer.receive_bytes(PREVIEW_CHUNK_BYTES), PREVIEW_IDLE_TIMEOUT_SECONDS
        ):
            if len(data) > PREVIEW_CHUNK_BYTES:
                raise ValueError("preview message exceeds chunk bound")
            writer.write(data)
            await asyncio.wait_for(writer.drain(), PREVIEW_IDLE_TIMEOUT_SECONDS)

    tasks = (asyncio.create_task(to_peer()), asyncio.create_task(from_peer()))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        await peer.aclose()


@dataclass(slots=True)
class MacForward:
    port: int
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    closed: bool = False


class MacPreviewForwards:
    """A lease fixes the Mac loopback port before any stream can dial it."""

    def __init__(
        self,
        open_stream: Callable[[str], AbstractAsyncContextManager[PreviewPeer]],
        valid: Callable[[], bool],
    ) -> None:
        self.open_stream, self.valid = open_stream, valid
        self.leases: dict[str, MacForward] = {}
        self.closed = False
        self._streams: dict[str, asyncio.Task[None]] = {}

    async def bind(self, lease_id: str, port: int) -> None:
        if (
            not self.valid()
            or self.closed
            or not _valid_id(lease_id)
            or type(port) is not int
            or not 1 <= port <= 65535
        ):
            raise ValueError("invalid or stale preview lease")
        if lease_id in self.leases or len(self.leases) >= MAX_PREVIEW_LISTENERS:
            raise ValueError("preview listener capacity or duplicate lease")
        self.leases[lease_id] = MacForward(port)

    def open(self, lease_id: str, stream_id: str, port: int) -> None:
        lease = self.leases.get(lease_id)
        if (
            self.closed
            or not self.valid()
            or lease is None
            or lease.closed
            or type(port) is not int
            or port != lease.port
            or not _valid_id(stream_id)
            or stream_id in self._streams
            or len(self._streams) >= MAX_PREVIEW_STREAMS
        ):
            raise ValueError("invalid, stale, or unavailable preview stream")

        async def serve() -> None:
            local_writer = None
            try:
                async with AsyncExitStack() as stack:
                    async with asyncio.timeout(PREVIEW_OPEN_TIMEOUT_SECONDS):
                        local_reader, local_writer = await asyncio.open_connection(
                            "127.0.0.1", port
                        )
                        peer = await stack.enter_async_context(self.open_stream(stream_id))
                    if self.closed or lease.closed or not self.valid():
                        raise ConnectionError("preview generation changed")
                    await _bridge_peer(local_reader, local_writer, peer)
            except (ConnectionError, OSError, TimeoutError, ValueError):
                logger.debug("Mac preview stream unavailable", exc_info=True)
            finally:
                if local_writer is not None:
                    local_writer.close()

        # Both registries reserve capacity before the coroutine can run.
        task = asyncio.create_task(serve())
        self._streams[stream_id] = task
        lease.tasks.add(task)

        def finished(task: asyncio.Task[None]) -> None:
            self._streams.pop(stream_id, None)
            lease.tasks.discard(task)

        task.add_done_callback(finished)

    async def release(self, lease_id: str) -> None:
        lease = self.leases.pop(lease_id, None)
        if lease is None:
            raise ValueError("unknown preview lease")
        lease.closed = True
        tasks = tuple(lease.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self.closed = True
        await asyncio.gather(*(self.release(key) for key in tuple(self.leases)))


@dataclass(slots=True)
class ForwardLease:
    server: asyncio.Server
    port: int
    lease_id: str
    users: int = 0
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    failed: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False


@dataclass(slots=True)
class PendingStream:
    lease: ForwardLease
    peer: asyncio.Future[PreviewPeer]
    done: asyncio.Event = field(default_factory=asyncio.Event)
    attached: bool = False


class PreviewForwards:
    def __init__(
        self,
        request: Callable[[WireMessageKind, JsonObject], Awaitable[WireMessage]],
        valid: Callable[[], bool],
    ) -> None:
        self.request, self.valid = request, valid
        self.leases: dict[tuple[str, int], ForwardLease] = {}
        self._streams: dict[str, PendingStream] = {}
        self._lock = asyncio.Lock()
        self.closed = False

    async def attach(self, stream_id: str, peer: PreviewPeer) -> None:
        stream = self._streams.get(stream_id)
        if (
            self.closed
            or not self.valid()
            or stream is None
            or stream.lease.closed
            or stream.attached
            or stream.peer.done()
        ):
            raise ValueError("unknown, stale, or consumed preview stream")
        stream.attached = True
        stream.peer.set_result(peer)
        try:
            await stream.done.wait()
        finally:
            await peer.aclose()

    @asynccontextmanager
    async def lease(self, port: int, ticket_id: str) -> AsyncIterator[int]:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid preview port")
        key = ticket_id, port
        async with self._lock:
            if self.closed or not self.valid():
                raise ConnectionError("Mac executor is unavailable")
            lease = self.leases.get(key)
            if lease is None:
                if len(self.leases) >= MAX_PREVIEW_LISTENERS:
                    raise ConnectionError("preview listener capacity reached")
                lease_id = uuid4().hex
                try:
                    response = await asyncio.wait_for(
                        self.request(
                            WireMessageKind.preview_bind, {"lease_id": lease_id, "port": port}
                        ),
                        PREVIEW_OPEN_TIMEOUT_SECONDS,
                    )
                    if response.kind is not WireMessageKind.preview_bound or not self.valid():
                        raise ConnectionError("preview forward refused")

                    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                        assert lease is not None
                        if (
                            self.closed
                            or lease.closed
                            or not self.valid()
                            or len(self._streams) >= MAX_PREVIEW_STREAMS
                        ):
                            writer.close()
                            return
                        stream_id = uuid4().hex
                        stream = PendingStream(lease, asyncio.get_running_loop().create_future())
                        self._streams[stream_id] = stream

                        async def serve() -> None:
                            assert lease is not None
                            peer = None
                            try:
                                async with asyncio.timeout(PREVIEW_OPEN_TIMEOUT_SECONDS):
                                    reply = await self.request(
                                        WireMessageKind.preview_open,
                                        {
                                            "lease_id": lease_id,
                                            "stream_id": stream_id,
                                            "port": port,
                                        },
                                    )
                                    if reply.kind is not WireMessageKind.preview_bound:
                                        raise ConnectionError("preview stream refused")
                                    peer = await stream.peer
                                if self.closed or lease.closed or not self.valid():
                                    raise ConnectionError("preview generation changed")
                                await _bridge_peer(reader, writer, peer)
                            except (OSError, ConnectionError, TimeoutError, ValueError):
                                logger.debug("preview stream unavailable", exc_info=True)
                                lease.failed.set()
                            finally:
                                writer.close()
                                if (
                                    peer is None
                                    and stream.peer.done()
                                    and not stream.peer.cancelled()
                                ):
                                    peer = stream.peer.result()
                                self._streams.pop(stream_id, None)
                                stream.done.set()
                                if peer is not None:
                                    await peer.aclose()

                        task = asyncio.create_task(serve())
                        lease.tasks.add(task)
                        task.add_done_callback(lease.tasks.discard)

                    server = await asyncio.start_server(accept, "127.0.0.1", 0)
                    lease = ForwardLease(server, int(server.sockets[0].getsockname()[1]), lease_id)
                    self.leases[key] = lease
                except BaseException:
                    if self.valid():
                        with contextlib.suppress(
                            ConnectionError, OSError, ValueError, TimeoutError
                        ):
                            await asyncio.wait_for(
                                self.request(
                                    WireMessageKind.preview_release, {"lease_id": lease_id}
                                ),
                                PREVIEW_OPEN_TIMEOUT_SECONDS,
                            )
                    raise
            lease.users += 1
        try:
            yield lease.port
            if self.closed or not self.valid() or lease.failed.is_set():
                raise ConnectionError("Mac preview connection ended before request completion")
        finally:
            async with self._lock:
                lease.users -= 1
                if lease.users == 0 and self.leases.get(key) is lease:
                    self.leases.pop(key)
                    await self._close_lease(lease, notify=True)

    async def _close_lease(self, lease: ForwardLease, *, notify: bool) -> None:
        lease.closed = True
        lease.server.close()
        tasks = tuple(lease.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task canceled before its first instruction cannot run its finally block.
        for stream_id, stream in tuple(self._streams.items()):
            if stream.lease is lease:
                self._streams.pop(stream_id, None)
                stream.done.set()
                if stream.peer.done() and not stream.peer.cancelled():
                    await stream.peer.result().aclose()
                else:
                    stream.peer.cancel()
        await lease.server.wait_closed()
        if notify and self.valid():
            with contextlib.suppress(ConnectionError, OSError, ValueError, TimeoutError):
                await asyncio.wait_for(
                    self.request(WireMessageKind.preview_release, {"lease_id": lease.lease_id}),
                    PREVIEW_OPEN_TIMEOUT_SECONDS,
                )

    async def close(self) -> None:
        async with self._lock:
            self.closed = True
            leases = tuple(self.leases.values())
            self.leases.clear()
            results = await asyncio.gather(
                *(self._close_lease(lease, notify=False) for lease in leases),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
