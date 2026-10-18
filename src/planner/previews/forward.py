"""Request-scoped leases for private VPS listeners on the current executor generation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from planner.previews.configuration import MAX_PREVIEW_LISTENERS
from planner.previews.streams import PreviewStreams


@dataclass(slots=True)
class ForwardLease:
    server: asyncio.Server
    port: int
    users: int = 0
    stream_ids: set[str] = field(default_factory=set)
    failed: asyncio.Event = field(default_factory=asyncio.Event)


class PreviewForwards:
    def __init__(self, streams: PreviewStreams) -> None:
        self.streams = streams
        self.leases: dict[tuple[str, int], ForwardLease] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def lease(self, port: int, ticket_id: str) -> AsyncIterator[int]:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid preview port")
        key = (ticket_id, port)
        async with self._lock:
            if not self.streams.available():
                raise ConnectionError("Mac executor is unavailable")
            lease = self.leases.get(key)
            if lease is None:
                if len(self.leases) >= MAX_PREVIEW_LISTENERS:
                    raise ConnectionError("preview listener capacity reached")
                accepted_ids: set[str] = set()
                failed = asyncio.Event()

                def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                    stream_id = self.streams.accept(reader, writer, port)
                    if stream_id is not None:
                        accepted_ids.add(stream_id)
                        stream = self.streams.streams[stream_id]
                        stream.on_failure = failed.set
                        task = stream.task
                        assert task is not None

                        def forget(_task: asyncio.Task[None]) -> None:
                            accepted_ids.discard(stream_id)

                        task.add_done_callback(forget)

                server = await asyncio.start_server(
                    accept,
                    host="127.0.0.1",
                    port=0,
                )
                lease = ForwardLease(
                    server,
                    int(server.sockets[0].getsockname()[1]),
                    stream_ids=accepted_ids,
                    failed=failed,
                )
                self.leases[key] = lease
            lease.users += 1
        try:
            yield lease.port
            # Buffered HTTP can treat a truncated close-delimited body as success.
            # Preserve transport failure across that EOF until the request exits.
            if not self.streams.available() or lease.failed.is_set():
                raise ConnectionError("Mac preview connection ended before request completion")
        finally:
            async with self._lock:
                lease.users -= 1
                if lease.users == 0:
                    if self.leases.get(key) is lease:
                        del self.leases[key]
                    lease.server.close()
                    await self.streams.close_streams(lease.stream_ids)
                    await lease.server.wait_closed()

    async def close(self) -> None:
        async with self._lock:
            leases = tuple(self.leases.values())
            for lease in leases:
                lease.server.close()
            self.leases.clear()
            # Python 3.13 wait_closed also waits for accepted sockets. Stop streams
            # first so a stalled local reader cannot keep the listener alive.
            await self.streams.close()
            for lease in leases:
                await lease.server.wait_closed()
