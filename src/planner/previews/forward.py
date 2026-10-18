"""Generation-bound preview leases over SSH streamlocal forwarding."""

from __future__ import annotations

import asyncio
import contextlib
import os
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import asyncssh

from planner.executor.configuration import SSH_CLOSE_TIMEOUT_SECONDS, SSH_UNIX_SOCKET_MAX_BYTES
from planner.executor.contracts import (
    ByteReader,
    ByteWriter,
    JsonObject,
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


async def copy_streams(reader: ByteReader, writer: ByteWriter) -> None:
    """The SSH channel supplies flow control; each application wait has an idle bound."""
    while data := await asyncio.wait_for(
        reader.read(PREVIEW_CHUNK_BYTES), PREVIEW_IDLE_TIMEOUT_SECONDS
    ):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), PREVIEW_IDLE_TIMEOUT_SECONDS)


async def bridge(
    left_reader: ByteReader,
    left_writer: ByteWriter,
    right_reader: ByteReader,
    right_writer: ByteWriter,
) -> None:
    tasks = (
        asyncio.create_task(copy_streams(left_reader, right_writer)),
        asyncio.create_task(copy_streams(right_reader, left_writer)),
    )
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for writer in (left_writer, right_writer):
            writer.close()


@dataclass(slots=True)
class MacForward:
    listener: asyncssh.SSHListener | None = None
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    closed: bool = False


class MacPreviewForwards:
    """Only server-minted paths and fixed Mac loopback ports enter SSH forwarding."""

    def __init__(
        self, connection: asyncssh.SSHClientConnection, root: Path, valid: Callable[[], bool]
    ) -> None:
        self.connection, self.root, self.valid = connection, root, valid
        self.leases: dict[str, MacForward] = {}
        self.closed = False
        self._accepted_channels = 0

    async def bind(self, path: str, port: int) -> None:
        target = Path(path)
        if (
            not self.valid()
            or self.closed
            or target.parent != self.root
            or len(target.name) != 32
            or any(c not in "0123456789abcdef" for c in target.name)
            or type(port) is not int
            or not 1 <= port <= 65535
        ):
            raise ValueError("invalid or stale preview lease")
        if path in self.leases or len(self.leases) >= MAX_PREVIEW_LISTENERS:
            raise ValueError("preview listener capacity or duplicate lease")
        lease = MacForward()
        self.leases[path] = lease

        def handler_factory() -> Callable[
            [asyncssh.SSHReader[bytes], asyncssh.SSHWriter[bytes]], Awaitable[None]
        ]:
            # Called at channel admission, before AsyncSSH starts the coroutine.
            if (
                lease.closed
                or not self.valid()
                or self.closed
                or self._accepted_channels >= MAX_PREVIEW_STREAMS
            ):
                raise asyncssh.ChannelOpenError(
                    asyncssh.OPEN_RESOURCE_SHORTAGE, "preview unavailable"
                )

            self._accepted_channels += 1

            async def serve(
                reader: asyncssh.SSHReader[bytes], writer: asyncssh.SSHWriter[bytes]
            ) -> None:
                task = asyncio.current_task()
                assert task is not None
                lease.tasks.add(task)
                local_writer = None
                try:
                    if lease.closed or not self.valid():
                        raise ConnectionError("preview generation changed")
                    local_reader, local_writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", port), PREVIEW_OPEN_TIMEOUT_SECONDS
                    )
                    await bridge(reader, writer, local_reader, local_writer)
                except (ConnectionError, OSError, TimeoutError):
                    pass
                finally:
                    writer.close()
                    if local_writer is not None:
                        local_writer.close()
                    lease.tasks.discard(task)
                    self._accepted_channels -= 1

            return serve

        try:
            lease.listener = await asyncio.wait_for(
                self.connection.start_unix_server(handler_factory, path),
                PREVIEW_OPEN_TIMEOUT_SECONDS,
            )
            if not self.valid() or lease.closed:
                raise ConnectionError("preview generation changed")
        except BaseException:
            if self.leases.get(path) is lease:
                await self.release(path)
            elif lease.listener is not None:
                lease.listener.close()
                await asyncio.wait_for(lease.listener.wait_closed(), SSH_CLOSE_TIMEOUT_SECONDS)
            raise

    async def release(self, path: str) -> None:
        lease = self.leases.pop(path, None)
        if lease is None:
            raise ValueError("unknown preview lease")
        lease.closed = True
        try:
            if lease.listener is not None:
                lease.listener.close()
        finally:
            tasks = tuple(lease.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if lease.listener is not None:
            await asyncio.wait_for(lease.listener.wait_closed(), SSH_CLOSE_TIMEOUT_SECONDS)

    async def close(self) -> None:
        self.closed = True
        results = await asyncio.gather(
            *(self.release(path) for path in tuple(self.leases)), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result


@dataclass(slots=True)
class ForwardLease:
    server: asyncio.Server
    port: int
    path: Path
    socket_identity: tuple[int, int]
    users: int = 0
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    failed: asyncio.Event = field(default_factory=asyncio.Event)


class PreviewForwards:
    def __init__(
        self,
        root: Path,
        request: Callable[[WireMessageKind, JsonObject], Awaitable[WireMessage]],
        valid: Callable[[], bool],
    ) -> None:
        self.root, self.request, self.valid = root, request, valid
        self.leases: dict[tuple[str, int], ForwardLease] = {}
        self._lock = asyncio.Lock()
        self.closed = False

    def _remove_socket(self, path: Path, identity: tuple[int, int]) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if (
            path.parent != self.root
            or info.st_uid != os.getuid()
            or not stat.S_ISSOCK(info.st_mode)
            or (info.st_dev, info.st_ino) != identity
        ):
            raise ValueError("unsafe preview socket")
        path.unlink()

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
                path = self.root / uuid4().hex
                if len(os.fsencode(path)) > SSH_UNIX_SOCKET_MAX_BYTES:
                    raise ValueError("preview Unix path is too long")
                info: os.stat_result | None = None
                try:
                    response = await asyncio.wait_for(
                        self.request(
                            WireMessageKind.preview_bind, {"path": str(path), "port": port}
                        ),
                        PREVIEW_OPEN_TIMEOUT_SECONDS,
                    )
                    if response.kind is not WireMessageKind.preview_bound or not self.valid():
                        raise ConnectionError("preview forward refused")
                    info = path.lstat()
                    if info.st_uid != os.getuid() or not stat.S_ISSOCK(info.st_mode):
                        raise ValueError("unsafe preview socket")

                    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                        if (
                            self.closed
                            or not self.valid()
                            or sum(len(item.tasks) for item in self.leases.values())
                            >= MAX_PREVIEW_STREAMS
                        ):
                            writer.close()
                            return

                        async def serve() -> None:
                            assert lease is not None
                            remote_writer = None
                            try:
                                remote_reader, remote_writer = await asyncio.wait_for(
                                    asyncio.open_unix_connection(path), PREVIEW_OPEN_TIMEOUT_SECONDS
                                )
                                await bridge(reader, writer, remote_reader, remote_writer)
                            except (OSError, ConnectionError, TimeoutError):
                                lease.failed.set()
                            finally:
                                writer.close()
                                if remote_writer is not None:
                                    remote_writer.close()

                        task = asyncio.create_task(serve())
                        assert lease is not None
                        lease.tasks.add(task)
                        task.add_done_callback(lease.tasks.discard)

                    server = await asyncio.start_server(accept, "127.0.0.1", 0)
                    lease = ForwardLease(
                        server,
                        int(server.sockets[0].getsockname()[1]),
                        path,
                        (info.st_dev, info.st_ino),
                    )
                    self.leases[key] = lease
                except BaseException:
                    # A lost bind reply can still leave a remote listener. Revoke
                    # the server-minted lease before this admission attempt exits.
                    if self.valid():
                        with contextlib.suppress(
                            ConnectionError, OSError, ValueError, TimeoutError
                        ):
                            await asyncio.wait_for(
                                self.request(WireMessageKind.preview_release, {"path": str(path)}),
                                PREVIEW_OPEN_TIMEOUT_SECONDS,
                            )
                    if info is not None:
                        self._remove_socket(path, (info.st_dev, info.st_ino))
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
        lease.server.close()
        for task in tuple(lease.tasks):
            task.cancel()
        await asyncio.gather(*lease.tasks, return_exceptions=True)
        await lease.server.wait_closed()
        if notify and self.valid():
            with contextlib.suppress(ConnectionError, OSError, ValueError, TimeoutError):
                await asyncio.wait_for(
                    self.request(WireMessageKind.preview_release, {"path": str(lease.path)}),
                    PREVIEW_OPEN_TIMEOUT_SECONDS,
                )
        self._remove_socket(lease.path, lease.socket_identity)

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
