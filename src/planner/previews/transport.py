"""Bounded HTTP/WebSocket transport to an observed, pinned local process.

The connector verifies the accepted socket before aiohttp can write a request or
WebSocket handshake. Each request has its own force-closed connector.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Any, cast

import aiohttp
from aiohttp.client_proto import ResponseHandler
from aiohttp.tracing import Trace
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect
from yarl import URL

from planner.previews import config
from planner.previews.contracts import PreviewError, PreviewRecord
from planner.previews.limits import Capacity, MessageBudget, TransferBudget
from planner.previews.policy import request_headers, response_headers, within_prefix
from planner.previews.service import PreviewService


class PreviewClientSession(aiohttp.ClientSession):
    """aiohttp's WebSocket handshake also uses _request: deny its redirects too."""

    async def _request(self, *args: Any, **kwargs: Any) -> aiohttp.ClientResponse:
        kwargs["allow_redirects"] = False
        return await super()._request(*args, **kwargs)


class HeaderBoundedProtocol(ResponseHandler):
    """Admit an entire bounded header block before aiohttp allocates its fields."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(loop)
        self.header_pending = True
        self.header_bytes = bytearray()
        self.header_admitted = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        # Socket evidence may await an executor. Do not read an eager server's
        # unsolicited body into aiohttp's parser-free tail while it runs.
        self.pause_before_parser()

    def pause_before_parser(self) -> None:
        # aiohttp's pause_reading also pauses its HTTP parser and asserts one
        # exists. This gap deliberately has no parser yet.
        self._reading_paused = True
        if self.transport is not None:
            self.transport.pause_reading()

    def set_response_params(self, **kwargs: Any) -> None:
        self.resume_reading(resume_parser=False)
        super().set_response_params(**kwargs)

    def set_parser(
        self, parser: Any, payload: Any, data_received_cb: Callable[[], None] | None = None
    ) -> None:
        self.resume_reading()
        super().set_parser(parser, payload, data_received_cb)

    def data_received(self, data: bytes) -> None:
        if self._parser is None or (self._upgraded and self._payload_parser is None):
            self.pause_before_parser()
            if len(self._tail) + len(data) > config.WEBSOCKET_MESSAGE_BYTES + config.CHUNK_BYTES:
                self.close()
                self.set_exception(
                    PreviewError("Preview sent data before its parser was ready.", 502)
                )
                return
            super().data_received(data)
            return
        if not data:
            super().data_received(data)
            return
        while self.header_pending and data:
            # Only copy the bounded leading portion, even if a peer writes a
            # huge body and its headers in the same TCP packet.
            remaining = config.HEADER_BYTES - self.header_admitted - len(self.header_bytes)
            candidate = bytes(self.header_bytes) + data[:remaining]
            boundary = candidate.find(b"\r\n\r\n")
            if boundary < 0:
                if len(self.header_bytes) + len(data) + self.header_admitted >= config.HEADER_BYTES:
                    self.close()
                    self.set_exception(
                        PreviewError("Preview headers exceed the supported limit.", 502)
                    )
                    self.header_bytes.clear()
                    return
                self.header_bytes.extend(data)
                return
            header_end = boundary + 4
            consumed = header_end - len(self.header_bytes)
            block = candidate[:header_end]
            self.header_bytes.clear()
            self.header_admitted += header_end
            # Interim responses are also bounded; 101 switches to WS framing.
            status = block.split(b" ", 2)[1] if b" " in block else b""
            self.header_pending = status.startswith(b"1") and status != b"101"
            super().data_received(block)
            if status == b"101" and self._payload_parser is None:
                self.pause_reading()
            data = data[consumed:]
        if data:
            if self._upgraded and self._payload_parser is None:
                if (
                    len(self._tail) + len(data)
                    > config.WEBSOCKET_MESSAGE_BYTES + config.CHUNK_BYTES
                ):
                    self.close()
                    self.set_exception(PreviewError("Preview WebSocket handshake overflowed.", 502))
                    return
            super().data_received(data)


class VerifiedConnector(aiohttp.TCPConnector):
    def __init__(self, service: PreviewService, record: PreviewRecord) -> None:
        super().__init__(force_close=True, limit=1, use_dns_cache=False)
        self._factory = cast(
            partial[ResponseHandler], partial(HeaderBoundedProtocol, loop=self._loop)
        )
        self.service = service
        self.record = record

    async def _create_connection(
        self, req: aiohttp.ClientRequest, traces: list[Trace], timeout: aiohttp.ClientTimeout
    ) -> ResponseHandler:
        await self.service.before_connection(self.record)
        protocol = await super()._create_connection(req, traces, timeout)
        try:
            if protocol.transport is None:
                raise PreviewError("Preview application connection ended.", 410)
            sock = protocol.transport.get_extra_info("socket")
            if sock is None:
                raise PreviewError("Preview application socket could not be verified.", 410)
            await self.service.after_connection(self.record, int(sock.getsockname()[1]))
        except BaseException:
            protocol.close()
            raise
        return protocol


class PreviewResponse(StreamingResponse):
    def __init__(
        self,
        owner: PreviewTransport,
        record: PreviewRecord,
        session: aiohttp.ClientSession,
        upstream: aiohttp.ClientResponse,
        headers: list[tuple[str, str]],
        started: float,
    ) -> None:
        self.owner = owner
        self.record = record
        self.session = session
        self.upstream = upstream
        self.started = started
        super().__init__(self.chunks(), status_code=upstream.status)
        self.raw_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers
        ]

    async def chunks(self) -> AsyncIterator[bytes]:
        async for chunk in self.upstream.content.iter_chunked(config.CHUNK_BYTES):
            await self.owner.budget.consume(len(chunk))
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.owner.tasks[self.record.generation].add(task)

        async def bounded_send(message: Any) -> None:
            async with asyncio.timeout(config.DOWNSTREAM_SEND_SECONDS):
                await send(message)

        try:
            async with asyncio.timeout(
                max(0, config.HTTP_TOTAL_SECONDS - (time.monotonic() - self.started))
            ):

                async def disconnected() -> None:
                    while (await receive())["type"] != "http.disconnect":
                        await asyncio.sleep(0)

                streaming = asyncio.create_task(self.stream_response(bounded_send))
                disconnect = asyncio.create_task(disconnected())
                try:
                    done, _ = await asyncio.wait(
                        {streaming, disconnect}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for completed in done:
                        completed.result()
                finally:
                    streaming.cancel()
                    disconnect.cancel()
                    await asyncio.gather(streaming, disconnect, return_exceptions=True)
        finally:
            self.upstream.close()
            await self.owner.finish(self.record, self.session, websocket=False)
            self.owner.untrack(self.record.generation, task)


class PreviewTransport:
    def __init__(self, service: PreviewService) -> None:
        self.service = service
        self.http_capacity = Capacity(config.HTTP_GLOBAL, config.HTTP_PER_PREVIEW)
        self.websocket_capacity = Capacity(config.WEBSOCKET_GLOBAL, config.WEBSOCKET_PER_PREVIEW)
        self.budget = TransferBudget()
        self.cookie_names: dict[str, set[str]] = defaultdict(set)
        self.sessions: dict[str, set[aiohttp.ClientSession]] = defaultdict(set)
        self.tasks: dict[str, set[asyncio.Task[Any]]] = defaultdict(set)

    def session(self, record: PreviewRecord) -> aiohttp.ClientSession:
        session = PreviewClientSession(
            connector=VerifiedConnector(self.service, record),
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(
                total=config.HTTP_TOTAL_SECONDS,
                sock_connect=config.CONNECT_SECONDS,
                sock_read=config.HTTP_IDLE_SECONDS,
            ),
            read_bufsize=config.CHUNK_BYTES,
            max_line_size=config.HEADER_BYTES,
            max_field_size=config.HEADER_BYTES,
        )
        self.sessions[record.generation].add(session)
        return session

    async def finish(
        self, record: PreviewRecord, session: aiohttp.ClientSession, *, websocket: bool
    ) -> None:
        await session.close()
        self.forget_session(record.generation, session)
        capacity = self.websocket_capacity if websocket else self.http_capacity
        capacity.release(record.generation)

    def forget_session(self, generation: str, session: aiohttp.ClientSession) -> None:
        sessions = self.sessions.get(generation)
        if sessions is not None:
            sessions.discard(session)
            if not sessions:
                self.sessions.pop(generation, None)

    def untrack(self, generation: str, task: asyncio.Task[Any]) -> None:
        tasks = self.tasks.get(generation)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                self.tasks.pop(generation, None)

    async def revoke(self, generation: str) -> None:
        for task in tuple(self.tasks.pop(generation, set())):
            task.cancel()
        for session in tuple(self.sessions.pop(generation, set())):
            await session.close()
        self.cookie_names.pop(generation, None)

    async def close(self) -> None:
        for generation in set(self.sessions) | set(self.tasks):
            await self.revoke(generation)

    async def http(self, request: Request, ticket_id: str, generation: str) -> Response:
        session: aiohttp.ClientSession | None = None
        started = time.monotonic()
        admitted = False
        try:
            self.http_capacity.acquire(generation)
            admitted = True
            record = await self.service.check(ticket_id, generation)
            raw_path = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
            if not within_prefix(raw_path, record.base_path):
                raise PreviewError("Preview request must stay under its prepared base path.", 400)
            headers = request_headers(
                list(request.headers.items()),
                port=record.port,
                origin=record.public_origin,
                cookie_names=self.cookie_names[record.generation],
            )
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > config.REQUEST_BODY_BYTES):
                raise PreviewError("Preview request body exceeds the supported limit.", 413)

            async def body() -> AsyncIterator[bytes]:
                total = 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > config.REQUEST_BODY_BYTES:
                        raise PreviewError("Preview request body exceeds the supported limit.", 413)
                    for offset in range(0, len(chunk), config.CHUNK_BYTES):
                        part = chunk[offset : offset + config.CHUNK_BYTES]
                        await self.budget.consume(len(part))
                        yield part

            query = request.scope.get("query_string", b"").decode("ascii")
            url = f"http://127.0.0.1:{record.port}{raw_path}" + (f"?{query}" if query else "")
            session = self.session(record)
            async with asyncio.timeout(
                max(0, config.PREHEADER_SECONDS - (time.monotonic() - started))
            ):
                upstream = await session.request(
                    request.method,
                    URL(url, encoded=True),
                    headers=headers,
                    data=body(),
                    allow_redirects=False,
                )
                clean, cookies = response_headers(
                    list(upstream.headers.items()),
                    request_url=record.public_origin + raw_path,
                    origin=record.public_origin,
                    prefix=record.base_path,
                )
                await self.service.check(record.ticket_id, record.generation)
                names = self.cookie_names[record.generation]
                if len(names | cookies) > config.COOKIE_NAMES_PER_GENERATION:
                    raise PreviewError("Preview declares too many cookies.", 502)
                names.update(cookies)
            return PreviewResponse(self, record, session, upstream, clean, started)
        except (PreviewError, aiohttp.ClientError, TimeoutError) as exc:
            if session is not None:
                await session.close()
                self.forget_session(record.generation, session)
            if admitted:
                self.http_capacity.release(generation)
            status = exc.status_code if isinstance(exc, PreviewError) else 504
            message = (
                str(exc)
                if isinstance(exc, PreviewError)
                else "Preview application timed out or disconnected."
            )
            return PlainTextResponse(message, status_code=status)
        except BaseException:
            if session is not None:
                await self.finish(record, session, websocket=False)
            elif admitted:
                self.http_capacity.release(generation)
            raise

    async def websocket(self, websocket: WebSocket, ticket_id: str, generation: str) -> None:
        started = time.monotonic()
        session: aiohttp.ClientSession | None = None
        admitted = False
        task = asyncio.current_task()
        assert task is not None
        try:
            self.websocket_capacity.acquire(generation)
            admitted = True
            self.tasks[generation].add(task)
            record = await self.service.check(ticket_id, generation)
            path = websocket.scope.get("raw_path", websocket.url.path.encode()).decode("ascii")
            if not within_prefix(path, record.base_path):
                raise PreviewError("Preview WebSocket requires its generation base path.", 400)
            headers = request_headers(
                list(websocket.headers.items()),
                port=record.port,
                origin=record.public_origin,
                cookie_names=self.cookie_names[record.generation],
            )
            # aiohttp constructs its own WebSocket handshake, including these fields.
            headers = [
                (key, value)
                for key, value in headers
                if not key.lower().startswith("sec-websocket-")
            ]
            query = websocket.scope.get("query_string", b"").decode("ascii")
            url = f"http://127.0.0.1:{record.port}{path}" + (f"?{query}" if query else "")
            session = self.session(record)
            async with asyncio.timeout(
                max(0, config.PREHEADER_SECONDS - (time.monotonic() - started))
            ):
                peer = await session.ws_connect(
                    URL(url, encoded=True),
                    headers=headers,
                    protocols=websocket.scope.get("subprotocols", []),
                    heartbeat=config.WEBSOCKET_PING_SECONDS,
                    max_msg_size=config.WEBSOCKET_MESSAGE_BYTES,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=config.CLOSE_SECONDS),
                )
                await self.service.check(record.ticket_id, record.generation)
            try:
                await websocket.accept(subprotocol=peer.protocol)
            except RuntimeError as exc:
                # The browser may leave while the accepted socket is being proved.
                # Uvicorn then already considers the handshake closed.
                raise WebSocketDisconnect(code=1006) from exc
            messages = MessageBudget()

            async def upstream_pump() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    data = message.get("bytes")
                    if data is None:
                        data = message.get("text", "")
                    size = len(data.encode()) if isinstance(data, str) else len(data)
                    if size > config.WEBSOCKET_MESSAGE_BYTES:
                        raise PreviewError(
                            "Preview WebSocket message exceeds the supported limit.", 413
                        )
                    messages.consume()
                    await self.budget.consume(size)
                    async with asyncio.timeout(config.DOWNSTREAM_SEND_SECONDS):
                        if isinstance(data, str):
                            await peer.send_str(data)
                        else:
                            await peer.send_bytes(data)

            async def downstream_pump() -> None:
                async for message in peer:
                    if message.type not in {aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY}:
                        if message.type == aiohttp.WSMsgType.ERROR:
                            raise PreviewError("Preview WebSocket application disconnected.", 410)
                        continue
                    messages.consume()
                    data = message.data
                    await self.budget.consume(
                        len(data.encode()) if isinstance(data, str) else len(data)
                    )
                    async with asyncio.timeout(config.DOWNSTREAM_SEND_SECONDS):
                        if isinstance(data, str):
                            await websocket.send_text(data)
                        else:
                            await websocket.send_bytes(data)

            pumps = {asyncio.create_task(upstream_pump()), asyncio.create_task(downstream_pump())}
            try:
                done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    finished.result()
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                await peer.close()
        except (PreviewError, aiohttp.ClientError, TimeoutError, WebSocketDisconnect):
            try:
                async with asyncio.timeout(config.CLOSE_SECONDS):
                    await websocket.close(code=1013)
            except (RuntimeError, WebSocketDisconnect, TimeoutError):
                pass
        finally:
            if session is not None:
                await self.finish(record, session, websocket=True)
            elif admitted:
                self.websocket_capacity.release(generation)
            self.untrack(generation, task)
