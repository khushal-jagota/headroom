"""Machine-authenticated executor routes, with protocol 26 work admission.

Each request binds its credential to a named destination before it selects an owner.
The original configured executor keeps its prior authority only before first issuance.
Work requests also require that machine's current incarnation. Update requests retain
protocol-independent recovery, but never bypass permanent credential handover.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
from dataclasses import asdict, replace
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError
from starlette.requests import ClientDisconnect, HTTPConnection
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect as database_connect
from planner.core.response_compression import answers_uncompressed
from planner.core.task_settlement import run_on_worker_thread
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import UpdateConflict
from planner.execution_identity import ExecutorIdentity, ProtocolRefused, parse_identity
from planner.executor.configuration import (
    EXECUTOR_COMMAND_FILE_PATH,
    EXECUTOR_COMMAND_OUTCOMES_PATH,
    EXECUTOR_COMMANDS_PATH,
    EXECUTOR_CREDENTIAL_HEADER,
    EXECUTOR_CREDENTIAL_PATH,
    EXECUTOR_FILE_SHA256_HEADER,
    EXECUTOR_FILE_UPLOAD_HEADER,
    EXECUTOR_INCARNATION_HEADER,
    EXECUTOR_ITEMS_PATH,
    EXECUTOR_LEASE_PATH,
    EXECUTOR_LIVE_PATH,
    EXECUTOR_PROTOCOL_HEADER,
    EXECUTOR_READS_PATH,
    EXECUTOR_UPLOAD_PATH,
    EXECUTOR_WORKSPACE_REGISTRATION_PATH,
    EXECUTOR_WORKSPACE_RETIREMENT_PATH,
    EXECUTOR_WORKSPACE_STATES_PATH,
    FILE_TRANSFER_STALL_SECONDS,
    MAX_COMMAND_OUTCOME_REPORT_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_ITEM_BATCH_BYTES,
    MAX_MAILBOX_POLL_BYTES,
    MAX_WORKSPACE_STATES_BYTES,
    TRANSFER_CHUNK_BYTES,
)
from planner.executor.files import regular_descriptor
from planner.executor.protocol import (
    CommandOutcomeReport,
    FileUploadRequest,
    ItemBatch,
    LeaseRefused,
    LeaseRequest,
    LiveEventBatch,
    LiveReadRequest,
    WorkspaceRegistrationRequest,
    WorkspaceRetirementRequest,
    WorkspaceStatesRequest,
    decode_bounded,
    encode_bounded,
)
from planner.executor.update_configuration import MAX_MAC_UPDATE_RESPONSE_BYTES
from planner.executor.update_contracts import MacUpdateCheck, MacUpdateReport
from planner.executor.vps_files import InputFile
from planner.executor.vps_transport import VpsExecutor
from planner.machines import executor_credentials

_STATUS: dict[ErrorCode, int] = {
    ErrorCode.executor_version_refused: 409,
    ErrorCode.executor_incarnation_not_current: 409,
    ErrorCode.agent_forbidden: 403,
    ErrorCode.not_found: 404,
    ErrorCode.retry_later: 503,
}


class _BodyTooLarge(PlannerError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.validation, "executor request body exceeds its size limit")


def _error(error: PlannerError) -> JSONResponse:
    """Every non-2xx answer of a work route carries PlannerError.to_payload()."""
    status = 413 if isinstance(error, _BodyTooLarge) else _STATUS.get(error.code, 400)
    return JSONResponse(status_code=status, content=error.to_payload())


def _json(body: bytes, status: int = 200) -> Response:
    return Response(body, status_code=status, media_type="application/json")


class _StallBoundedFileResponse(Response):
    """Stream one input file. A peer that stops reading for FILE_TRANSFER_STALL_SECONDS
    fails the transfer, as one that stops sending does on the upload side."""

    def __init__(self, input_file: InputFile) -> None:
        super().__init__(
            status_code=200,
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(input_file.byte_count),
                EXECUTOR_FILE_SHA256_HEADER: input_file.sha256,
            },
        )
        self.input_file = input_file

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def bounded(message: dict[str, Any]) -> None:
            await asyncio.wait_for(send(message), FILE_TRANSFER_STALL_SECONDS)

        await bounded(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        with regular_descriptor(self.input_file.path) as descriptor:
            remaining = self.input_file.byte_count
            while remaining:
                chunk = await asyncio.to_thread(
                    os.read, descriptor, min(TRANSFER_CHUNK_BYTES, remaining)
                )
                if not chunk:
                    raise OSError("input file is shorter than its digest")
                remaining -= len(chunk)
                await bounded(
                    {"type": "http.response.body", "body": chunk, "more_body": bool(remaining)}
                )
        if not self.input_file.byte_count:
            await bounded({"type": "http.response.body", "body": b"", "more_body": False})


async def _body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise _BodyTooLarge()
    return bytes(body)


def _decode[T](body: bytes, value_type: type[T], limit: int) -> T:
    try:
        return decode_bounded(body, value_type, limit=limit)
    except (ValidationError, ProtocolRefused, ValueError) as error:
        raise PlannerError(ErrorCode.validation, f"malformed executor request: {error}") from error


def _lease_refusal(refusal: LeaseRefused) -> Response:
    try:
        body = encode_bounded(refusal)
    except ProtocolRefused:
        if refusal.update is None:
            raise
        body = encode_bounded(replace(refusal, update=None))
    return _json(body, 409)


def build_executor_router() -> APIRouter:
    from planner.core.trusted_ingress import executor_ingress_claim, require_executor_ingress

    router = APIRouter()

    def database_path(connection: HTTPConnection) -> str:
        registry = getattr(connection.app.state, "executor_registry", None)
        endpoint = getattr(connection.app.state, "executor_endpoint", None)
        owner = registry if registry is not None else endpoint
        if owner is not None:
            return str(owner.db_path)
        config = getattr(connection.app.state, "config", None)
        if config is not None:
            return str(config.db_path)
        raise PlannerError(ErrorCode.retry_later, "executor endpoint unavailable")

    def authenticate(connection: HTTPConnection, claimed_id: str) -> str:
        with database_connect(database_path(connection)) as conn:
            return executor_credentials.authenticate_executor(
                conn, claimed_id, connection.headers.get(EXECUTOR_CREDENTIAL_HEADER),
                connection.app.state.executor_id,
            )

    async def authenticated_machine(connection: HTTPConnection) -> str:
        try:
            claimed_id = executor_ingress_claim(connection.scope)
        except ValueError as error:
            raise PlannerError(ErrorCode.agent_forbidden, str(error)) from error
        return await run_on_worker_thread(authenticate, connection, claimed_id)

    async def endpoint_for(connection: HTTPConnection) -> VpsExecutor:
        """Authenticate the destination before selecting any runtime owner."""
        machine_id = await authenticated_machine(connection)
        registry = getattr(connection.app.state, "executor_registry", None)
        if registry is not None:
            return cast(VpsExecutor, await registry.ensure(machine_id))
        endpoint = getattr(connection.app.state, "executor_endpoint", None)
        if not isinstance(endpoint, VpsExecutor) or machine_id != endpoint.machine_id:
            raise PlannerError(ErrorCode.retry_later, "executor endpoint unavailable")
        return endpoint

    async def current_for(connection: HTTPConnection) -> tuple[VpsExecutor, ExecutorIdentity]:
        endpoint = await endpoint_for(connection)
        identity = endpoint.require_current(
            incarnation_header=connection.headers.get(EXECUTOR_INCARNATION_HEADER),
            protocol_header=connection.headers.get(EXECUTOR_PROTOCOL_HEADER),
        )
        return endpoint, identity

    async def updates_for(request: Request) -> tuple[MacUpdateService, VpsExecutor | None]:
        # Recovery remains available before protocol admission, with machine authority.
        try:
            machine_id = await authenticated_machine(request)
        except PlannerError as error:
            raise HTTPException(403, error.message) from error
        registry = getattr(request.app.state, "executor_registry", None)
        endpoint = (
            await registry.ensure(machine_id) if registry is not None
            else getattr(request.app.state, "executor_endpoint", None)
        )
        if isinstance(endpoint, VpsExecutor):
            endpoint.record_contact()
        service = getattr(request.app.state, "mac_updates", None)
        if not isinstance(service, MacUpdateService):
            raise HTTPException(503, "Mac update service unavailable")
        return service.for_machine(machine_id), endpoint

    @router.post(EXECUTOR_CREDENTIAL_PATH)
    async def original_credential(request: Request) -> Response:
        try:
            require_executor_ingress(request.scope, request.app.state.executor_id)
            if request.headers.get(EXECUTOR_CREDENTIAL_HEADER) is not None:
                raise PlannerError(
                    ErrorCode.agent_forbidden, "Credential handover is already closed"
                )
            raw = _decode(await _body(request, MAX_ENVELOPE_BYTES), dict, MAX_ENVELOPE_BYTES)
            identity = parse_identity(raw)

            def issue() -> dict[str, object]:
                conn = database_connect(database_path(request))
                try:
                    return asdict(executor_credentials.issue_original_credential(
                        conn, identity.executor_incarnation,
                    ))
                finally:
                    conn.close()

            grant = await run_on_worker_thread(issue)
            return JSONResponse(grant)
        except (ValueError, ProtocolRefused) as error:
            return _error(PlannerError(ErrorCode.agent_forbidden, str(error)))
        except PlannerError as error:
            return _error(error)

    # --- 1-8: the work and presence routes ------------------------------------------------

    @router.post(EXECUTOR_LEASE_PATH)
    async def lease(request: Request) -> Response:
        try:
            endpoint = await endpoint_for(request)
            # Any lease attempt past ingress is contact, whatever fails next, so a
            # failing lease cannot look like a sleeping Mac (mac_lease_failing).
            endpoint.record_contact()
            body = await _body(request, MAX_ENVELOPE_BYTES)
            raw = _decode(body, dict, MAX_ENVELOPE_BYTES)
            try:
                identity = parse_identity(raw.get("identity"), check_versions=False)
            except (ValidationError, ProtocolRefused, ValueError) as error:
                raise PlannerError(
                    ErrorCode.validation, f"malformed executor request: {error}"
                ) from error
            refusal = await endpoint.refuse_lease_version(identity)
            if refusal is not None:
                return _lease_refusal(refusal)
            lease_request = _decode(body, LeaseRequest, MAX_ENVELOPE_BYTES)
            reply = await endpoint.lease(lease_request)
        except PlannerError as error:
            return _error(error)
        if isinstance(reply, LeaseRefused):
            return _lease_refusal(reply)
        return _json(encode_bounded(reply))

    @router.post(EXECUTOR_WORKSPACE_STATES_PATH)
    async def workspace_states(request: Request) -> Response:
        try:
            endpoint, identity = await current_for(request)
            query = _decode(
                await _body(request, MAX_WORKSPACE_STATES_BYTES),
                WorkspaceStatesRequest,
                MAX_WORKSPACE_STATES_BYTES,
            )
            reply = await endpoint.workspace_states(identity, query)
            body = encode_bounded(reply, limit=MAX_WORKSPACE_STATES_BYTES)
        except PlannerError as error:
            return _error(error)
        return _json(body)

    @router.post(EXECUTOR_WORKSPACE_REGISTRATION_PATH)
    async def register_workspace(request: Request) -> Response:
        try:
            endpoint, identity = await current_for(request)
            registration = _decode(
                await _body(request, MAX_ENVELOPE_BYTES),
                WorkspaceRegistrationRequest,
                MAX_ENVELOPE_BYTES,
            )
            await endpoint.register_workspace(identity, registration)
        except PlannerError as error:
            return _error(error)
        return Response(status_code=204)

    @router.post(EXECUTOR_WORKSPACE_RETIREMENT_PATH)
    async def retire_workspace(request: Request) -> Response:
        try:
            endpoint, identity = await current_for(request)
            retirement = _decode(
                await _body(request, MAX_ENVELOPE_BYTES),
                WorkspaceRetirementRequest,
                MAX_ENVELOPE_BYTES,
            )
            reply = await endpoint.retire_workspace(
                identity, retirement.state, retirement.workspace, replay=retirement.replay
            )
        except PlannerError as error:
            return _error(error)
        return _json(encode_bounded(reply))

    @router.get(EXECUTOR_COMMANDS_PATH)
    async def poll_commands(request: Request) -> Response:
        try:
            endpoint, identity = await current_for(request)
            try:
                after = int(request.query_params.get("after", ""))
                wait = float(request.query_params.get("wait", "0"))
            except ValueError as error:
                raise PlannerError(ErrorCode.validation, "invalid poll parameters") from error
            reply = await endpoint.poll(identity, after, wait)
        except PlannerError as error:
            return _error(error)
        return _json(encode_bounded(reply, limit=MAX_MAILBOX_POLL_BYTES))

    @router.post(EXECUTOR_COMMAND_OUTCOMES_PATH)
    async def report_outcomes(request: Request) -> Response:
        try:
            endpoint, identity = await current_for(request)
            report = _decode(
                await _body(request, MAX_COMMAND_OUTCOME_REPORT_BYTES),
                CommandOutcomeReport,
                MAX_COMMAND_OUTCOME_REPORT_BYTES,
            )
            reply = await endpoint.record_outcomes(identity, report)
        except PlannerError as error:
            return _error(error)
        return _json(encode_bounded(reply))

    @router.post(EXECUTOR_ITEMS_PATH)
    async def post_items(request: Request, conversation_id: str) -> Response:
        try:
            endpoint, identity = await current_for(request)
            batch = _decode(
                await _body(request, MAX_ITEM_BATCH_BYTES), ItemBatch, MAX_ITEM_BATCH_BYTES
            )
            reply = await endpoint.accept_items(identity, conversation_id, batch)
        except PlannerError as error:
            # Retry later comes back inside the reply; an error here refuses the whole batch.
            return _error(error)
        return _json(encode_bounded(reply, limit=MAX_ITEM_BATCH_BYTES))

    @router.put(EXECUTOR_UPLOAD_PATH)
    async def upload(request: Request, sha256: str) -> Response:
        try:
            endpoint, identity = await current_for(request)
            if request.headers.get("Content-Encoding") or not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise PlannerError(ErrorCode.validation, "an upload is raw bytes named by digest")
            try:
                header = request.headers.get(EXECUTOR_FILE_UPLOAD_HEADER, "")
                described = base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))
            except (binascii.Error, ValueError) as error:
                raise PlannerError(ErrorCode.validation, "invalid upload description") from error
            upload_request = _decode(described, FileUploadRequest, MAX_ENVELOPE_BYTES)
            length = request.headers.get("Content-Length")
            await endpoint.upload(
                identity,
                sha256,
                upload_request,
                int(length) if length is not None and length.isdigit() else None,
                request.stream(),
            )
        except PlannerError as error:
            return _error(error)
        except ClientDisconnect:
            return Response(status_code=400)
        return Response(status_code=204)

    @router.get(EXECUTOR_COMMAND_FILE_PATH)
    @answers_uncompressed
    async def command_file(request: Request, command_id: str, stored_file_id: str) -> Response:
        try:
            endpoint, identity = await current_for(request)
            input_file = await endpoint.input_file(identity, command_id, stored_file_id)
        except PlannerError as error:
            return _error(error)
        return _StallBoundedFileResponse(input_file)

    @router.post(EXECUTOR_READS_PATH)
    async def read(request: Request) -> Response:
        try:
            endpoint, identity = await current_for(request)
            read_request = _decode(
                await _body(request, MAX_ENVELOPE_BYTES), LiveReadRequest, MAX_ENVELOPE_BYTES
            )
            value = await endpoint.read(identity, read_request)
        except PlannerError as error:
            return _error(error)
        return JSONResponse(value)

    @router.post(EXECUTOR_LIVE_PATH)
    async def live(request: Request, conversation_id: str) -> Response:
        try:
            endpoint, identity = await current_for(request)
            batch = _decode(
                await _body(request, MAX_ENVELOPE_BYTES), LiveEventBatch, MAX_ENVELOPE_BYTES
            )
            await endpoint.live(identity, conversation_id, batch)
        except PlannerError as error:
            return _error(error)
        return Response(status_code=204)

    # --- 9: the update channel, for a Mac of any version -----------------------------------

    # Mac runtimes in the field read these bodies without decoding transport encoding, so
    # the whole update channel skips compression. The archives are gzipped already.
    @router.get("/api/executor/update")
    @answers_uncompressed
    async def update_target(request: Request) -> MacUpdateCheck:
        try:
            service, _ = await updates_for(request)
            return await asyncio.to_thread(service.check)
        except (UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/api/executor/update/{request_id}/archive")
    @answers_uncompressed
    async def update_archive(request: Request, request_id: str) -> FileResponse:
        try:
            service, _ = await updates_for(request)
            path = await asyncio.to_thread(service.archive, request_id)
        except (UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return FileResponse(path, media_type="application/gzip")

    @router.get("/api/executor/update/prefetch/{archive_sha256}")
    @answers_uncompressed
    async def update_prefetch_archive(request: Request, archive_sha256: str) -> FileResponse:
        try:
            service, _ = await updates_for(request)
            path = await asyncio.to_thread(service.prefetch_archive, archive_sha256)
        except (UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return FileResponse(path, media_type="application/gzip")

    @router.post("/api/executor/update")
    @answers_uncompressed
    async def update_report(request: Request) -> dict[str, bool]:
        service, endpoint = await updates_for(request)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_MAC_UPDATE_RESPONSE_BYTES:
                raise HTTPException(413, "Mac update report exceeds its size limit")
        try:
            report = TypeAdapter(MacUpdateReport).validate_json(bytes(body), strict=True)
            # Do not yield between the presence snapshot and its receipt.
            service.report(report, endpoint.health() if isinstance(endpoint, VpsExecutor) else None)
        except (ValidationError, UpdateConflict, OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return {"confirmed": report.phase == "current"}

    # --- 10: the old WebSocket, refused ---------------------------------------------------

    @router.websocket("/api/executor/connect")
    async def connect(socket: WebSocket) -> None:
        """An old Mac's session. Closing before accept answers 403; the refusal is kept,
        so the Mac page and readiness say the Mac needs an update."""
        try:
            endpoint = await endpoint_for(socket)
        except PlannerError:
            await socket.close(code=1008)
            return
        await endpoint.refuse_old_websocket()
        await socket.close(code=1008)

    return router
