"""Capability-scoped agent CLI, receipt uncertainty and owner-only maintenance IPC."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Literal, cast

from planner.execution_identity import ChildReference
from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
)
from planner.executor.files import (
    safe_component,
)
from planner.executor.mac_connection import MacConnection
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner, RunningChild
from planner.executor.protocol import (
    AcceptanceReceipt,
    CliWrite,
    EnvelopeKind,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    IpcRetryPayload,
    IpcWritePayload,
    JsonObject,
    LiveReadRequest,
    OutboxEntry,
    QueuedEnvelope,
    QuiescenceReceipt,
    QuiesceRequest,
    ReceiptDisposition,
    UnconfirmedCliResult,
    WireMessageKind,
    as_object,
    encode_frame,
    read_typed_frame,
    type_adapter,
)


class AgentGateway:
    def __init__(
        self,
        *,
        runner: MacRunner,
        outbox: MacOutbox,
        connection: MacConnection,
        request_exit: Callable[[], None],
        settle_catalogs: Callable[[], Awaitable[None]],
    ) -> None:
        self.runner = runner
        self.spool = outbox
        self.connection = connection
        self._request_exit = request_exit
        self._settle_catalogs = settle_catalogs
        self._publishing: dict[str, tuple[ChildReference, asyncio.Task[QueuedEnvelope]]] = {}
        self.exit_committed = False
        self._quiesce_lock = asyncio.Lock()

    async def handle(self, request: IpcRequest) -> IpcResponse:
        try:
            if request.operation == IpcOperation.health:
                return IpcResponse(request.request_id, as_object(self.connection.health()), None)
            if request.operation == IpcOperation.quiesce:
                return await self._quiesce(request)
            child = self.runner.authenticate(request.child_id, request.capability)
            if request.operation == IpcOperation.read:
                return await self._read(request, child)
            if request.operation == IpcOperation.retry:
                return await self._retry(request, child)
            if request.operation != IpcOperation.write:
                raise ValueError("unsupported executor IPC operation")
            deadline = time.monotonic() + CLI_RESULT_TIMEOUT_SECONDS
            if request.response_deadline is not None:
                if not math.isfinite(request.response_deadline):
                    raise ValueError("invalid IPC response deadline")
                deadline = min(deadline, request.response_deadline)
            return await self._write(request, child, deadline)
        except (ValueError, PermissionError, ConnectionError) as error:
            code = "validation"
            if isinstance(error, PermissionError):
                code = "agent_forbidden"
            elif isinstance(error, ConnectionError):
                code = "connection"
            return IpcResponse(
                request.request_id, None, {"code": code, "message": str(error), "detail": {}}
            )

    async def _quiesce(self, request: IpcRequest) -> IpcResponse:
        maintenance = type_adapter(QuiesceRequest).validate_python(request.payload)
        async with self._quiesce_lock:
            self.connection.admission_stopped = True
            await self._settle_catalogs()
            quiescence = await self.runner.quiesce()
            exit_committed = quiescence.ready and maintenance.exit_when_ready
            if exit_committed:
                self.exit_committed = True
                self._request_exit()
            result = QuiescenceReceipt(
                self.connection.identity, os.getpid(), quiescence, exit_committed
            )
            return IpcResponse(request.request_id, as_object(result), None)

    async def _read(self, request: IpcRequest, child: RunningChild) -> IpcResponse:
        link = self.connection.ready_link()
        if link is None:
            raise ConnectionError("VPS unavailable; reads never use journal")
        response = await link.request(
            WireMessageKind.read_request,
            as_object(
                LiveReadRequest(
                    child.reference,
                    str(request.payload["path"]),
                    cast(JsonObject, request.payload.get("params", {})),
                )
            ),
        )
        return IpcResponse(request.request_id, response.payload, None)

    async def _retry(self, request: IpcRequest, child: RunningChild) -> IpcResponse:
        retry = type_adapter(IpcRetryPayload).validate_python(request.payload)
        submission_id = safe_component(retry.submission_id)
        publication = self._publishing.get(submission_id)
        if publication is not None:
            if publication[0] != child.reference:
                raise PermissionError("submission belongs to another executor child")
            return self._unconfirmed(request.request_id, submission_id, "preparing")
        row = await asyncio.to_thread(self.spool.lookup, submission_id)
        if row is None:
            return self._unconfirmed(request.request_id, submission_id, "unknown")
        if row.envelope.kind != EnvelopeKind.cli_write or (
            type_adapter(CliWrite).validate_python(row.envelope.payload).child != child.reference
        ):
            raise PermissionError("submission belongs to another executor child")
        if row.pending:
            return self._unconfirmed(request.request_id, submission_id, "queued")
        assert row.receipt is not None
        return self._receipt_response(request.request_id, row.receipt)

    async def _write(
        self, request: IpcRequest, child: RunningChild, deadline: float
    ) -> IpcResponse:
        incoming = type_adapter(IpcWritePayload).validate_python(request.payload)
        submission_id = safe_component(request.request_id)
        if self.runner.admission_stopped:
            raise PermissionError("executor admission stopped")
        write = CliWrite(
            incoming.operation, as_object(incoming.arguments), child.reference, child.active_turn
        )
        entry = OutboxEntry(
            child.reference.conversation_id, EnvelopeKind.cli_write, as_object(write), submission_id
        )
        task = self.runner.source_work(
            child.reference,
            partial(asyncio.to_thread, self.spool.add, entry, ticket_id=child.ticket_id),
        )
        self._publishing[submission_id] = (child.reference, task)
        task.add_done_callback(lambda _: self._publishing.pop(submission_id, None))
        done, _ = await asyncio.wait((task,), timeout=max(0, deadline - time.monotonic()))
        if not done:
            return self._unconfirmed(request.request_id, submission_id, "preparing")
        if task.cancelled() or task.exception() is not None:
            return self._unconfirmed(request.request_id, submission_id, "unknown")
        receipt = await self.spool.wait_confirmed(
            submission_id, max(0, deadline - time.monotonic())
        )
        if receipt is None:
            return self._unconfirmed(request.request_id, submission_id, "queued")
        return self._receipt_response(request.request_id, receipt)

    @staticmethod
    def _unconfirmed(
        request_id: str,
        submission_id: str,
        publication: Literal["unknown", "preparing", "queued"],
    ) -> IpcResponse:
        return IpcResponse(
            request_id,
            None,
            None,
            unconfirmed=UnconfirmedCliResult(request_id, submission_id, publication),
        )

    @staticmethod
    def _receipt_response(request_id: str, receipt: AcceptanceReceipt) -> IpcResponse:
        if receipt.disposition == ReceiptDisposition.refused:
            error_payload = receipt.result.get("error", receipt.result)
            return IpcResponse(request_id, None, cast(JsonObject, error_payload))
        return IpcResponse(request_id, receipt.result, None)

    async def _ipc_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_typed_frame(reader, IpcRequest)
            response = await self.handle(request)
            writer.write(encode_frame(response))
            await writer.drain()
        except (ConnectionError, OSError):
            # Lost response delivery says nothing about an admitted publication.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    @asynccontextmanager
    async def serve(self, socket_path: Path) -> AsyncIterator[None]:
        socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._ipc_client, path=socket_path)
        socket_path.chmod(0o600)
        try:
            async with server:
                yield
        finally:
            socket_path.unlink(missing_ok=True)
