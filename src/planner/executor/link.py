"""The executor's standard app-service WebSocket and HTTP transport."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import aiohttp
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from planner.execution_identity import ExecutorIdentity, ProtocolRefused, parse_identity
from planner.executor.clock import now_ms
from planner.executor.configuration import (
    EXECUTOR_GENERATION_HEADER,
    EXECUTOR_ID_HEADER,
    EXECUTOR_INCARNATION_HEADER,
    HTTP_CLOSE_TIMEOUT_SECONDS,
    HTTP_CONNECT_TIMEOUT_SECONDS,
    MAX_ENVELOPE_BYTES,
    MAX_TRANSFERS,
    REQUEST_TIMEOUT_SECONDS,
    WS_HEARTBEAT_SECONDS,
    validate_server_url,
)
from planner.executor.protocol import (
    ControlMessageReader,
    ControlMessageWriter,
    ExecutorHealth,
    JsonObject,
    WebSocketPeer,
    WireMessage,
    WireMessageKind,
    decode_message,
    encode_message,
    encode_value,
    type_adapter,
)


class ServerWebSocketPeer:
    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed or self.socket.client_state is WebSocketState.DISCONNECTED

    async def receive_bytes(self, max_bytes: int) -> bytes:
        try:
            message = await self.socket.receive()
        except (WebSocketDisconnect, RuntimeError):
            self._closed = True
            return b""
        if message["type"] == "websocket.disconnect":
            self._closed = True
            return b""
        data = message.get("bytes")
        if not isinstance(data, bytes) or not data or len(data) > max_bytes:
            raise ProtocolRefused("invalid WebSocket binary message")
        return data

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError("executor WebSocket closed")
        try:
            await self.socket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError, OSError) as error:
            raise ConnectionError("executor WebSocket closed") from error

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(RuntimeError, WebSocketDisconnect, OSError):
                await asyncio.wait_for(self.socket.close(), HTTP_CLOSE_TIMEOUT_SECONDS)


class ClientWebSocketPeer:
    def __init__(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self.socket = socket

    @property
    def closed(self) -> bool:
        return self.socket.closed

    async def receive_bytes(self, max_bytes: int) -> bytes:
        try:
            message = await self.socket.receive()
        except (aiohttp.ClientError, OSError) as error:
            raise ConnectionError("executor WebSocket failed") from error
        if message.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        ):
            return b""
        if message.type is aiohttp.WSMsgType.ERROR:
            raise ConnectionError("executor WebSocket failed") from self.socket.exception()
        if message.type is not aiohttp.WSMsgType.BINARY:
            raise ProtocolRefused("expected WebSocket binary message")
        data = message.data
        if not isinstance(data, bytes) or not data or len(data) > max_bytes:
            raise ProtocolRefused("invalid WebSocket binary message")
        return data

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError("executor WebSocket closed")
        try:
            await self.socket.send_bytes(data)
        except (aiohttp.ClientError, OSError) as error:
            raise ConnectionError("executor WebSocket failed") from error

    async def aclose(self) -> None:
        try:
            await asyncio.wait_for(self.socket.close(), HTTP_CLOSE_TIMEOUT_SECONDS)
        except (aiohttp.ClientError, OSError) as error:
            raise ConnectionError("executor WebSocket failed") from error


class ControlWebSocket:
    """One bounded, validated control message per binary WebSocket message."""

    def __init__(self, peer: WebSocketPeer) -> None:
        self.peer = peer
        self._closing: asyncio.Task[None] | None = None

    async def receive(self) -> WireMessage:
        body = await self.peer.receive_bytes(MAX_ENVELOPE_BYTES)
        if not body:
            raise ConnectionError("executor WebSocket closed")
        return decode_message(body)

    async def send(self, message: WireMessage) -> None:
        if self.is_closing():
            raise ConnectionError("executor WebSocket closed")
        await self.peer.send_bytes(encode_message(message))

    def is_closing(self) -> bool:
        return self._closing is not None or self.peer.closed

    def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self.peer.aclose())

    async def wait_closed(self) -> None:
        self.close()
        if self._closing is not None:
            await self._closing


def create_client_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        auto_decompress=False,
        timeout=aiohttp.ClientTimeout(
            total=None, sock_connect=HTTP_CONNECT_TIMEOUT_SECONDS, sock_read=REQUEST_TIMEOUT_SECONDS
        ),
    )


class ExecutorAppClient:
    def __init__(
        self,
        server_url: str,
        executor_id: str,
        identity: ExecutorIdentity,
        session_factory: Callable[[], aiohttp.ClientSession] = create_client_session,
    ) -> None:
        self.server_url = validate_server_url(server_url)
        if (
            not executor_id
            or executor_id != executor_id.strip()
            or any(char in executor_id for char in "\r\n\0")
        ):
            raise ValueError("executor ID must be explicit and safe")
        self.executor_id, self.identity = executor_id, identity
        self.session = session_factory()
        trace = aiohttp.TraceConfig()

        async def refuse_redirect(*args: Any) -> None:
            raise ConnectionError("executor transport refuses redirects")

        trace.on_request_redirect.append(refuse_redirect)
        trace.freeze()
        self.session.trace_configs.append(trace)

    def headers(self) -> dict[str, str]:
        return {
            EXECUTOR_ID_HEADER: self.executor_id,
            EXECUTOR_INCARNATION_HEADER: self.identity.executor_incarnation,
            EXECUTOR_GENERATION_HEADER: str(self.identity.connection_generation),
        }

    @asynccontextmanager
    async def websocket(self, path: str, max_bytes: int) -> AsyncIterator[ClientWebSocketPeer]:
        url = "wss" + self.server_url[5:] + path
        # Bound only connection establishment. Steady-state receives use heartbeat.
        async with asyncio.timeout(HTTP_CONNECT_TIMEOUT_SECONDS):
            socket = await self.session.ws_connect(
                url,
                headers=self.headers(),
                compress=0,
                heartbeat=WS_HEARTBEAT_SECONDS,
                max_msg_size=max_bytes + 1,
                timeout=aiohttp.ClientWSTimeout(ws_close=HTTP_CLOSE_TIMEOUT_SECONDS),
            )
        peer = ClientWebSocketPeer(socket)
        try:
            yield peer
        finally:
            await peer.aclose()

    async def close(self) -> None:
        await asyncio.wait_for(self.session.close(), HTTP_CLOSE_TIMEOUT_SECONDS)


class Link:
    """One connection generation, shared by both ends; no domain state or retry.

    Handlers run independently of receipt matching. Returning a message replies to
    the incoming request; malformed items are refused without losing the link.
    ``admit`` runs synchronously at the send point for domain checks which must
    follow backpressure (for example withdrawing work waiting behind a send).
    """

    def __init__(
        self,
        identity: ExecutorIdentity,
        reader: ControlMessageReader,
        writer: ControlMessageWriter,
        *,
        client: ExecutorAppClient | None = None,
    ) -> None:
        self.identity = identity
        self._reader, self._writer, self._client = reader, writer, client
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[WireMessage]] = {}
        self._handlers: dict[
            WireMessageKind, Callable[[WireMessage], Awaitable[WireMessage | None]]
        ] = {}
        self._work: set[asyncio.Task[None]] = set()
        self._reading: asyncio.Task[None] | None = None
        self._down = False
        self.last_received_at_ms: int | None = None
        self._capacity = asyncio.Semaphore(MAX_TRANSFERS)

    def on(
        self,
        kind: WireMessageKind,
        handler: Callable[[WireMessage], Awaitable[WireMessage | None]],
    ) -> None:
        self._handlers[kind] = handler

    def _start(self) -> asyncio.Task[None]:
        if self._reading is None:
            self._reading = asyncio.create_task(self._receive())
        return self._reading

    def is_closing(self) -> bool:
        return self._down or self._writer.is_closing()

    def close(self) -> None:
        if not self._down:
            self._down = True
            self._writer.close()
            if self._reading is not None and self._reading is not asyncio.current_task():
                self._reading.cancel()
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(ConnectionError("executor link lost"))

    async def wait_closed(self) -> None:
        self.close()
        await self._writer.wait_closed()
        if self._reading is not None:
            await asyncio.gather(self._reading, return_exceptions=True)
        # Admitted domain handlers settle under their owner, across reconnects.

    async def closed(self) -> None:
        try:
            await asyncio.shield(self._start())
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            if self._down:
                raise ConnectionError("executor link lost") from None
            raise

    async def tell(
        self,
        kind: WireMessageKind,
        payload: JsonObject,
        *,
        request_id: str | None = None,
        admit: Callable[[], bool] | None = None,
    ) -> bool:
        async with self._lock:
            if self.is_closing():
                return False
            if admit is not None and not admit():
                return False
            try:
                await self._writer.send(WireMessage(kind, request_id or uuid4().hex, payload))
            except (ConnectionError, OSError):
                self.close()
                raise
            return True

    async def request(
        self,
        kind: WireMessageKind,
        payload: JsonObject,
        *,
        timeout: float | None = None,
        request_id: str | None = None,
        close_on_timeout: bool = True,
        admit: Callable[[], bool] | None = None,
    ) -> WireMessage:
        self._start()
        request_id = request_id or uuid4().hex
        if request_id in self._pending:
            raise ValueError("duplicate link request ID")
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS if timeout is None else timeout):
                if not await self.tell(kind, payload, request_id=request_id, admit=admit):
                    raise ConnectionError("executor link unavailable")
                response = await future
            if kind is WireMessageKind.hello:
                if (
                    response.kind is not WireMessageKind.hello_accepted
                    or parse_identity(response.payload) != self.identity
                ):
                    self.close()
                    raise ProtocolRefused("handshake identity changed")
            if response.kind is WireMessageKind.error:
                raise ValueError(str(response.payload))
            return response
        except ProtocolRefused:
            self.close()
            raise
        except TimeoutError:
            if close_on_timeout:
                self.close()
            raise
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _dispatch(self, message: WireMessage) -> None:
        try:
            if self.is_closing():
                return
            response = await self._handlers[message.kind](message)
            if response is not None:
                await self.tell(response.kind, response.payload, request_id=message.request_id)
        except ProtocolRefused:
            self.close()
            raise
        except (ValueError, TypeError, KeyError) as error:
            await self.tell(
                WireMessageKind.error,
                {"error": {"code": "validation", "message": str(error)}},
                request_id=message.request_id,
            )
        except Exception:
            self.close()

    async def _receive(self) -> None:
        try:
            while not self.is_closing():
                message = await self._reader.receive()
                if self.is_closing():
                    raise ConnectionError("executor generation revoked")
                self.last_received_at_ms = now_ms()
                if message.kind is WireMessageKind.protocol_refused:
                    raise ProtocolRefused("executor protocol refused")
                future = self._pending.get(message.request_id)
                if future is not None:
                    if not future.done():
                        future.set_result(message)
                elif message.kind in self._handlers:
                    task = asyncio.create_task(self._dispatch(message))
                    self._work.add(task)
                    task.add_done_callback(self._handler_done)
        finally:
            self.close()

    def _handler_done(self, task: asyncio.Task[None]) -> None:
        self._work.discard(task)
        if not task.cancelled():
            task.exception()

    @asynccontextmanager
    async def transfer_capacity(self) -> AsyncIterator[None]:
        async with self._capacity:
            if self.is_closing():
                raise ConnectionError("executor file link unavailable")
            yield

    def file_client(self) -> ExecutorAppClient:
        if self.is_closing() or self._client is None:
            raise ConnectionError("executor file link unavailable")
        return self._client


@asynccontextmanager
async def dial_link(
    server_url: str,
    executor_id: str,
    identity: ExecutorIdentity,
    session_factory: Callable[[], aiohttp.ClientSession] = create_client_session,
) -> AsyncIterator[Link]:
    client = ExecutorAppClient(server_url, executor_id, identity, session_factory)
    link = None
    try:
        async with client.websocket("/api/executor/connect", MAX_ENVELOPE_BYTES) as peer:
            stream = ControlWebSocket(peer)
            link = Link(identity, stream, stream, client=client)
            yield link
    finally:
        if link is not None:
            link.close()
        try:
            await client.close()
        finally:
            if link is not None:
                await link.wait_closed()


async def accept_link(
    reader: ControlMessageReader,
    admit: Callable[[ExecutorHealth, Link], Awaitable[ExecutorIdentity]],
    *,
    writer: ControlMessageWriter,
) -> Link:
    """Read and validate the handshake before giving a generation to the owner."""
    link = None
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            hello = await reader.receive()
            if hello.kind is not WireMessageKind.hello:
                raise ProtocolRefused("executor handshake required")
            identity = parse_identity(hello.payload.get("identity"))
            health = type_adapter(ExecutorHealth).validate_python(hello.payload)
            if health.identity != identity:
                raise ProtocolRefused("identity coercion refused")
            if type(hello.payload.get("process_id")) is not int or health.process_id < 1:
                raise ProtocolRefused("invalid executor process identity")
            link = Link(identity, reader, writer)
            if await admit(health, link) != identity:
                raise ProtocolRefused("handshake identity changed")
            if not await link.tell(
                WireMessageKind.hello_accepted,
                cast(JsonObject, encode_value(identity)),
                request_id=hello.request_id,
            ):
                raise ConnectionError("executor link lost during handshake")
            return link
    except BaseException:
        if link is not None:
            link.close()
        else:
            writer.close()
        raise
