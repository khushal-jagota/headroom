"""App routes for the executor Link and its generation-owned file slots."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from pydantic import TypeAdapter, ValidationError
from starlette.requests import HTTPConnection
from starlette.responses import FileResponse, StreamingResponse

from planner.core.response_compression import answers_uncompressed
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import UpdateConflict
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import EXECUTOR_GENERATION_HEADER, EXECUTOR_INCARNATION_HEADER
from planner.executor.link import ServerWebSocketPeer
from planner.executor.update_configuration import MAX_MAC_UPDATE_RESPONSE_BYTES
from planner.executor.update_contracts import MacUpdateCheck, MacUpdateReport
from planner.executor.vps_transport import VpsExecutor


def build_executor_router() -> APIRouter:
    from planner.core.trusted_ingress import require_executor_ingress

    router = APIRouter()

    def require_ingress(connection: HTTPConnection) -> None:
        try:
            require_executor_ingress(connection.scope, connection.app.state.executor_id)
        except ValueError as error:
            raise HTTPException(403, str(error)) from error

    def endpoint_for(connection: HTTPConnection) -> VpsExecutor:
        require_ingress(connection)
        endpoint = getattr(connection.app.state, "executor_endpoint", None)
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

    def updates_for(request: Request) -> MacUpdateService:
        # Recovery must remain available before work-protocol/generation admission.
        require_ingress(request)
        service = getattr(request.app.state, "mac_updates", None)
        if not isinstance(service, MacUpdateService):
            raise HTTPException(503, "Mac update service unavailable")
        return service

    # Mac runtimes in the field read these bodies without decoding transport encoding, so
    # the whole update channel skips compression. The archives are gzipped already.
    @router.get("/api/executor/update")
    @answers_uncompressed
    async def update_target(request: Request) -> MacUpdateCheck:
        try:
            return await asyncio.to_thread(updates_for(request).check)
        except (UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/api/executor/update/{request_id}/archive")
    @answers_uncompressed
    async def update_archive(request: Request, request_id: str) -> FileResponse:
        try:
            path = await asyncio.to_thread(updates_for(request).archive, request_id)
        except (UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return FileResponse(path, media_type="application/gzip")

    @router.get("/api/executor/update/prefetch/{archive_sha256}")
    @answers_uncompressed
    async def update_prefetch_archive(request: Request, archive_sha256: str) -> FileResponse:
        try:
            path = await asyncio.to_thread(updates_for(request).prefetch_archive, archive_sha256)
        except (UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return FileResponse(path, media_type="application/gzip")

    @router.post("/api/executor/update")
    @answers_uncompressed
    async def update_report(request: Request) -> dict[str, bool]:
        service = updates_for(request)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_MAC_UPDATE_RESPONSE_BYTES:
                raise HTTPException(413, "Mac update report exceeds its size limit")
        try:
            report = TypeAdapter(MacUpdateReport).validate_json(bytes(body), strict=True)
            endpoint = getattr(request.app.state, "executor_endpoint", None)
            health = endpoint.health() if endpoint is not None else {}
            # Do not yield between the live generation snapshot and its receipt.
            service.report(report, health)
        except (ValidationError, UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return {"confirmed": report.phase == "current"}

    @router.websocket("/api/executor/connect")
    async def connect(socket: WebSocket) -> None:
        try:
            endpoint = endpoint_for(socket)
        except HTTPException:
            await socket.close(code=1008)
            return
        await socket.accept()
        stream = ServerWebSocketPeer(socket)
        try:
            await endpoint.session(stream)
        finally:
            await stream.wait_closed()

    @router.put("/api/executor/files/{transfer_id}")
    async def upload(request: Request, transfer_id: str) -> Response:
        endpoint = endpoint_for(request)
        identity = generation_for(request, endpoint)
        service = endpoint.transfers.get(identity)
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
        service = endpoint.transfers.get(identity)
        if service is None:
            raise HTTPException(409, "file generation unavailable")
        try:
            chunks = service.claim_download(transfer_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return StreamingResponse(chunks, media_type="application/octet-stream")

    return router
