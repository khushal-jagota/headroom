"""The executor's standard app-service WebSocket and HTTP transport."""

from __future__ import annotations

import asyncio
import contextlib
import re
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from planner.executor.configuration import (
    EXECUTOR_GENERATION_HEADER,
    EXECUTOR_ID_HEADER,
    EXECUTOR_INCARNATION_HEADER,
    HTTP_CLOSE_TIMEOUT_SECONDS,
    HTTP_CONNECT_TIMEOUT_SECONDS,
    MAX_ENVELOPE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    TRANSFER_CHUNK_BYTES,
    WS_HEARTBEAT_SECONDS,
)
from planner.executor.contracts import ExecutorIdentity, FileTransfer, PreviewPeer
from planner.executor.files import regular_descriptor
from planner.executor.transfers import settled_io
from planner.executor.wire import ProtocolRefused


def validate_server_url(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("executor server URL contains whitespace or control characters")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("executor server URL has invalid port") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and port < 1)
    ):
        raise ValueError("executor server URL must be an HTTPS origin")
    return value.rstrip("/")


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
    """One complete length-prefixed frame per binary message, with no input queue."""

    def __init__(self, peer: PreviewPeer) -> None:
        self.peer = peer
        self._incoming = b""
        self._outgoing: bytes | None = None
        self._closing: asyncio.Task[None] | None = None

    async def readexactly(self, n: int) -> bytes:
        if not self._incoming:
            frame = await self.peer.receive_bytes(MAX_ENVELOPE_BYTES + 4)
            if not frame:
                raise asyncio.IncompleteReadError(b"", n)
            if len(frame) < 4 or struct.unpack("!I", frame[:4])[0] != len(frame) - 4:
                raise ProtocolRefused("WebSocket message must contain exactly one frame")
            if not 0 < len(frame) - 4 <= MAX_ENVELOPE_BYTES:
                raise ProtocolRefused("invalid frame length")
            self._incoming = frame
        if n > len(self._incoming):
            raise ProtocolRefused("frame boundary mismatch")
        result, self._incoming = self._incoming[:n], self._incoming[n:]
        return result

    def write(self, data: bytes) -> None:
        if self.is_closing() or self._outgoing is not None:
            raise ConnectionError("executor writer closed or concurrent write")
        self._outgoing = data

    async def drain(self) -> None:
        data, self._outgoing = self._outgoing, None
        if data is not None:
            await self.peer.send_bytes(data)

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

    async def upload(self, slot: FileTransfer, path: Path) -> None:
        if slot.direction != "upload" or not re.fullmatch(r"[a-f0-9]{32}", slot.transfer_id):
            raise ValueError("invalid upload slot")

        async def chunks() -> AsyncIterator[bytes]:
            with regular_descriptor(path) as descriptor:
                remaining = slot.manifest.byte_count
                while remaining:
                    content = await settled_io(
                        os_read, descriptor, min(TRANSFER_CHUNK_BYTES, remaining)
                    )
                    if not content:
                        raise ValueError("snapshot shorter than manifest")
                    remaining -= len(content)
                    yield content
                if await settled_io(os_read, descriptor, 1):
                    raise ValueError("snapshot exceeds manifest")

        headers = self.headers() | {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(slot.manifest.byte_count),
        }
        async with self.session.put(
            self.server_url + "/api/executor/files/" + slot.transfer_id,
            data=chunks(),
            headers=headers,
            allow_redirects=False,
            compress=False,
        ) as response:
            if response.status != 204:
                raise ConnectionError("executor upload refused")

    async def download(
        self,
        slot: FileTransfer,
        path: Path,
        progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> None:
        import os

        from planner.executor.transfers import _write_all

        if slot.direction != "download" or not re.fullmatch(r"[a-f0-9]{32}", slot.transfer_id):
            raise ValueError("invalid download slot")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            async with self.session.get(
                self.server_url + "/api/executor/files/" + slot.transfer_id,
                headers=self.headers() | {"Accept-Encoding": "identity"},
                allow_redirects=False,
            ) as response:
                if response.status != 200 or response.headers.get("Content-Encoding"):
                    raise ConnectionError("executor download refused")
                count = 0
                async for chunk in response.content.iter_chunked(TRANSFER_CHUNK_BYTES):
                    count += len(chunk)
                    if count > slot.manifest.byte_count:
                        raise ValueError("download exceeds manifest")
                    await settled_io(_write_all, descriptor, chunk)
                    if progress is not None and chunk:
                        await progress("download", count)
                if count != slot.manifest.byte_count:
                    raise ValueError("download shorter than manifest")
                if progress is not None:
                    await progress("finalization", count)
                await settled_io(os.fsync, descriptor)
        finally:
            os.close(descriptor)

    async def close(self) -> None:
        await asyncio.wait_for(self.session.close(), HTTP_CLOSE_TIMEOUT_SECONDS)


def os_read(descriptor: int, count: int) -> bytes:
    import os

    return os.read(descriptor, count)


def build_executor_router() -> APIRouter:
    from planner.core.trusted_ingress import require_executor_ingress

    router = APIRouter()

    def endpoint_for(connection: Any) -> Any:
        try:
            require_executor_ingress(connection.scope, connection.app.state.executor_id)
        except ValueError as error:
            raise HTTPException(403, str(error)) from error
        endpoint = getattr(connection.app.state, "executor_endpoint", None)
        if endpoint is None:
            raise HTTPException(503, "executor endpoint unavailable")
        return endpoint

    def generation_for(connection: Any, endpoint: Any) -> Any:
        incarnation = connection.headers.get(EXECUTOR_INCARNATION_HEADER, "")
        generation = connection.headers.get(EXECUTOR_GENERATION_HEADER, "")
        identity = endpoint.identity
        if (
            identity is None
            or endpoint.writer is None
            or endpoint.writer.is_closing()
            or identity.executor_incarnation != incarnation
            or not re.fullmatch(r"[1-9][0-9]*", generation)
            or str(identity.connection_generation) != generation
        ):
            raise HTTPException(409, "executor generation is not current")
        return identity

    @router.websocket("/api/executor/connect")
    async def connect(socket: WebSocket) -> None:
        try:
            endpoint = endpoint_for(socket)
        except HTTPException:
            await socket.close(code=1008)
            return
        await socket.accept()
        stream = ControlWebSocket(ServerWebSocketPeer(socket))
        try:
            await endpoint._session(stream, stream)
        finally:
            await stream.wait_closed()

    @router.put("/api/executor/files/{transfer_id}")
    async def upload(request: Request, transfer_id: str) -> Response:
        endpoint = endpoint_for(request)
        identity = generation_for(request, endpoint)
        service = endpoint._transports.get(identity)
        if service is None or request.headers.get("Content-Encoding"):
            raise HTTPException(409, "file generation unavailable or encoded body")
        try:
            await service.upload(transfer_id, request.stream())
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return Response(status_code=204)

    @router.get("/api/executor/files/{transfer_id}")
    async def download(request: Request, transfer_id: str) -> Response:
        endpoint = endpoint_for(request)
        identity = generation_for(request, endpoint)
        service = endpoint._transports.get(identity)
        if service is None:
            raise HTTPException(409, "file generation unavailable")
        try:
            chunks = service.claim_download(transfer_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return StreamingResponse(chunks, media_type="application/octet-stream")

    @router.websocket("/api/executor/previews/{stream_id}")
    async def preview(socket: WebSocket, stream_id: str) -> None:
        try:
            endpoint = endpoint_for(socket)
            generation_for(socket, endpoint)
            forwards = endpoint._preview_forwards
            if forwards is None or not endpoint.ready:
                raise HTTPException(409, "preview generation unavailable")
        except HTTPException:
            await socket.close(code=1008)
            return
        await socket.accept()
        peer = ServerWebSocketPeer(socket)
        try:
            await forwards.attach(stream_id, peer)
        except (ValueError, ConnectionError, OSError):
            pass
        finally:
            await peer.aclose()

    return router
