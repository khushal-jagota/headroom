"""Bounded, generation-scoped byte streams on the executor's existing framed link."""

from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from uuid import uuid4

from planner.executor.contracts import ExecutorIdentity, JsonObject, WireMessage, WireMessageKind
from planner.previews.configuration import (
    MAX_PREVIEW_STREAMS,
    PREVIEW_CHUNK_BYTES,
    PREVIEW_IDLE_TIMEOUT_SECONDS,
    PREVIEW_OPEN_TIMEOUT_SECONDS,
)
from planner.previews.contracts import (
    PreviewClose,
    PreviewCredit,
    PreviewData,
    PreviewError,
    PreviewOpen,
    PreviewOpened,
    decode_content,
    decode_preview,
    preview_identity,
)


class PreviewStream:
    """One chunk of credit per direction; all socket waits live outside the link reader."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.opened = asyncio.Event()
        self.credit = asyncio.Event()
        self.credit.set()
        self.outstanding_bytes = 0
        self.receiving_bytes = 0
        self.incoming: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
        self.remote_closed = False
        self.last_activity = asyncio.get_running_loop().time()
        self.error: str | None = None
        self.failed = False
        self.on_failure: Callable[[], None] | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.task: asyncio.Task[None] | None = None


class PreviewStreams:
    """Admission never waits for capacity, a socket, or peer credit in the shared reader."""

    def __init__(
        self,
        identity: ExecutorIdentity,
        send: Callable[[WireMessage], Awaitable[None]],
        ready: Callable[[], bool],
        *,
        accept_open: bool,
    ) -> None:
        self.identity = identity
        self.send = send
        self.ready = ready
        self.accept_open = accept_open
        self.streams: dict[str, PreviewStream] = {}
        self.closed = False
        self._rejections: asyncio.Queue[WireMessage] = asyncio.Queue(maxsize=MAX_PREVIEW_STREAMS)
        self._rejection_task: asyncio.Task[None] | None = None

    def available(self) -> bool:
        return not self.closed and self.ready()

    def _reserve(self, stream_id: str) -> PreviewStream:
        if not self.available():
            raise ConnectionError("preview executor is unavailable")
        if len(self.streams) >= MAX_PREVIEW_STREAMS:
            raise ConnectionError("preview stream capacity reached")
        if stream_id in self.streams:
            raise ValueError("duplicate preview stream")
        stream = PreviewStream(stream_id)
        self.streams[stream_id] = stream
        return stream

    async def _send(
        self, kind: WireMessageKind, stream: PreviewStream, extra: JsonObject | None = None
    ) -> None:
        if not self.available():
            raise ConnectionError("preview executor is unavailable")
        payload = preview_identity(self.identity)
        if extra is not None:
            payload.update(extra)
        await asyncio.wait_for(
            self.send(WireMessage(kind, stream.stream_id, payload)), PREVIEW_IDLE_TIMEOUT_SECONDS
        )

    def feed(self, message: WireMessage) -> None:
        """Decode and admit synchronously; at most one task per bounded live stream."""
        payload = decode_preview(message, self.identity)
        if not self.available():
            raise ValueError("preview frame before reconciliation ready")
        if isinstance(payload, PreviewOpen):
            if not self.accept_open:
                raise ValueError("VPS does not accept preview opens")
            if len(self.streams) >= MAX_PREVIEW_STREAMS:
                self._reject_open(message.request_id)
                return
            admitted = self._reserve(message.request_id)
            self._start(admitted, self._serve(admitted, payload.port))
            return
        stream = self.streams.get(message.request_id)
        if stream is None:
            # A canceled HTTP exchange can leave a bounded in-flight chunk or credit.
            return
        stream.last_activity = asyncio.get_running_loop().time()
        if isinstance(payload, PreviewOpened):
            if self.accept_open or stream.opened.is_set():
                raise ValueError("unexpected preview open acknowledgment")
            stream.opened.set()
        elif isinstance(payload, PreviewCredit):
            if stream.outstanding_bytes != payload.byte_count:
                raise ValueError("preview credit does not match outstanding bytes")
            stream.outstanding_bytes = 0
            stream.credit.set()
        elif isinstance(payload, PreviewData):
            if not stream.opened.is_set() or stream.receiving_bytes or stream.remote_closed:
                raise ValueError("preview peer exceeded its receive credit")
            data = decode_content(payload.content_base64)
            stream.receiving_bytes = len(data)
            stream.incoming.put_nowait(data)
        elif isinstance(payload, PreviewClose):
            if not stream.remote_closed:
                stream.remote_closed = True
                stream.incoming.put_nowait(None)
                stream.opened.set()
        elif isinstance(payload, PreviewError):
            stream.error = payload.message
            stream.failed = True
            stream.remote_closed = True
            stream.opened.set()
            if stream.task is not None:
                stream.task.cancel()

    def _reject_open(self, stream_id: str) -> None:
        payload = preview_identity(self.identity)
        payload["message"] = "preview stream capacity reached"
        try:
            self._rejections.put_nowait(
                WireMessage(WireMessageKind.preview_error, stream_id, payload)
            )
        except asyncio.QueueFull as error:
            raise ValueError("preview rejection capacity reached") from error
        if self._rejection_task is None or self._rejection_task.done():
            self._rejection_task = asyncio.create_task(self._send_rejections())

    async def _send_rejections(self) -> None:
        try:
            while not self._rejections.empty():
                message = self._rejections.get_nowait()
                await asyncio.wait_for(self.send(message), PREVIEW_IDLE_TIMEOUT_SECONDS)
        except (ConnectionError, OSError):
            pass

    def accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int
    ) -> str | None:
        """Synchronous listener callback rejects overload without creating another task."""
        try:
            stream = self._reserve(str(uuid4()))
        except (ConnectionError, ValueError):
            writer.close()
            return None
        stream.writer = writer
        self._start(stream, self._forward(stream, reader, writer, port))
        return stream.stream_id

    def _start(self, stream: PreviewStream, coroutine: Coroutine[None, None, None]) -> None:
        task = asyncio.create_task(coroutine)
        stream.task = task

        def completed(result: asyncio.Task[None]) -> None:
            # A task canceled before its first instruction cannot run its finally.
            if self.streams.get(stream.stream_id) is stream:
                stream.failed = True
                if stream.on_failure is not None:
                    stream.on_failure()
                if stream.writer is not None:
                    stream.writer.transport.abort()
                self.streams.pop(stream.stream_id, None)
            if not result.cancelled():
                result.exception()

        task.add_done_callback(completed)

    async def _forward(
        self,
        stream: PreviewStream,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        port: int,
    ) -> None:
        try:
            await self._send(WireMessageKind.preview_open, stream, {"port": port})
            await asyncio.wait_for(stream.opened.wait(), PREVIEW_OPEN_TIMEOUT_SECONDS)
            if stream.remote_closed:
                raise ConnectionError(stream.error or "remote preview closed")
            await self._pump(stream, reader, writer)
        except (OSError, ValueError, TimeoutError):
            stream.failed = True
        finally:
            await self._finish(stream)

    async def _serve(self, stream: PreviewStream, port: int) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), PREVIEW_OPEN_TIMEOUT_SECONDS
            )
            stream.writer = writer
            stream.opened.set()
            await self._send(WireMessageKind.preview_opened, stream)
            await self._pump(stream, reader, writer)
        except (OSError, ValueError, TimeoutError):
            stream.failed = True
            with contextlib.suppress(ConnectionError, OSError):
                await self._send(
                    WireMessageKind.preview_error, stream, {"message": "Mac preview unavailable"}
                )
        finally:
            await self._finish(stream)

    async def _pump(
        self, stream: PreviewStream, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        async def outgoing() -> None:
            while True:
                # Wait before read and before EOF close: peer must drain the previous
                # chunk before a full close can cancel its pumps.
                await stream.credit.wait()
                data = await reader.read(PREVIEW_CHUNK_BYTES)
                if not data:
                    return
                stream.last_activity = asyncio.get_running_loop().time()
                stream.credit.clear()
                stream.outstanding_bytes = len(data)
                await self._send(
                    WireMessageKind.preview_data,
                    stream,
                    {"content_base64": base64.b64encode(data).decode("ascii")},
                )
                await asyncio.sleep(0)

        async def incoming() -> None:
            while True:
                data = await stream.incoming.get()
                if data is None:
                    return
                writer.write(data)
                await asyncio.wait_for(writer.drain(), PREVIEW_IDLE_TIMEOUT_SECONDS)
                stream.receiving_bytes = 0
                stream.last_activity = asyncio.get_running_loop().time()
                await self._send(WireMessageKind.preview_credit, stream, {"byte_count": len(data)})

        async def idle() -> None:
            while True:
                remaining = (
                    stream.last_activity
                    + PREVIEW_IDLE_TIMEOUT_SECONDS
                    - asyncio.get_running_loop().time()
                )
                if remaining <= 0:
                    raise TimeoutError("preview stream idle timeout")
                await asyncio.sleep(remaining)

        tasks = (
            asyncio.create_task(outgoing()),
            asyncio.create_task(incoming()),
            asyncio.create_task(idle()),
        )
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _finish(self, stream: PreviewStream) -> None:
        current = asyncio.current_task()
        canceled = current is not None and current.cancelling() > 0
        if canceled:
            stream.failed = True
        if stream.failed and stream.on_failure is not None:
            stream.on_failure()
        if canceled and stream.writer is not None:
            stream.writer.transport.abort()
        try:
            if not stream.remote_closed:
                with contextlib.suppress(ConnectionError, OSError):
                    await asyncio.wait_for(
                        self._send(WireMessageKind.preview_close, stream),
                        PREVIEW_OPEN_TIMEOUT_SECONDS,
                    )
        finally:
            try:
                if stream.writer is not None:
                    stream.writer.close()
                    try:
                        await asyncio.wait_for(
                            stream.writer.wait_closed(), PREVIEW_IDLE_TIMEOUT_SECONDS
                        )
                    except (ConnectionError, OSError):
                        stream.writer.transport.abort()
            finally:
                self.streams.pop(stream.stream_id, None)

    async def close_streams(self, stream_ids: set[str]) -> None:
        tasks = [
            stream.task
            for stream_id, stream in self.streams.items()
            if stream_id in stream_ids and stream.task is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self.closed = True
        tasks = [stream.task for stream in self.streams.values() if stream.task is not None]
        if self._rejection_task is not None:
            tasks.append(self._rejection_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.streams.clear()
