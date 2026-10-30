"""App routes for the executor Link and its generation-owned file slots."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from starlette.requests import HTTPConnection
from starlette.responses import StreamingResponse

from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import EXECUTOR_GENERATION_HEADER, EXECUTOR_INCARNATION_HEADER
from planner.executor.link import ControlWebSocket, ServerWebSocketPeer
from planner.executor.vps_transport import VpsExecutor


def build_executor_router() -> APIRouter:
    from planner.core.trusted_ingress import require_executor_ingress

    router = APIRouter()

    def endpoint_for(connection: HTTPConnection) -> VpsExecutor:
        try:
            require_executor_ingress(connection.scope, connection.app.state.executor_id)
        except ValueError as error:
            raise HTTPException(403, str(error)) from error
        endpoint = getattr(connection.app.state, "executor_endpoint", None)
        if endpoint is None:
            raise HTTPException(503, "executor endpoint unavailable")
        if not isinstance(endpoint, VpsExecutor):
            raise HTTPException(503, "executor endpoint unavailable")
        return endpoint

    def generation_for(connection: HTTPConnection, endpoint: VpsExecutor) -> ExecutorIdentity:
        incarnation = connection.headers.get(EXECUTOR_INCARNATION_HEADER, "")
        generation = connection.headers.get(EXECUTOR_GENERATION_HEADER, "")
        identity = endpoint.runs.identity
        if (
            identity is None
            or endpoint.runs.link is None
            or endpoint.runs.link.is_closing()
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
            await endpoint.store_upload(identity, transfer_id)
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

    return router
