"""Capability-scoped agent CLI, receipt uncertainty and owner-only maintenance IPC."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from planner.core.task_settlement import wait_for_settlement
from planner.execution_identity import (
    ExecutorIdentity,
)
from planner.executor.clock import now_ms
from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
)
from planner.executor.files import (
    safe_component,
)
from planner.executor.link import Link
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    AcceptanceReceipt,
    CliWrite,
    ConnectionState,
    EnvelopeKind,
    ExecutorHealth,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    IpcRetryPayload,
    IpcWritePayload,
    JsonObject,
    LiveReadRequest,
    OutboxEntry,
    OutboxRecord,
    QueuedCliResult,
    QuiescenceReceipt,
    QuiesceRequest,
    ReceiptDisposition,
    UnconfirmedCliResult,
    WireMessage,
    WireMessageKind,
    as_object,
    encode_frame,
    read_typed_frame,
    type_adapter,
)


class _IpcConflict(ValueError):
    """A conflicting reuse is refused before this attempt can publish."""


@dataclass
class _IpcPublication:
    write: CliWrite
    task: asyncio.Task[OutboxRecord]
    observers: int = 0
    admitted: bool = False


class AgentGateway:
    def __init__(
        self,
        *,
        runner: MacRunner,
        outbox: MacOutbox,
        identity: Callable[[], ExecutorIdentity],
        settle_catalogs: Callable[[], Awaitable[None]],
    ) -> None:
        self.runner = runner
        self.spool = outbox
        self._identity = identity
        self._settle_catalogs = settle_catalogs
        self._link: Link | None = None
        self._handshake_accepted = False
        self.state = ConnectionState.disconnected
        self.lost_at_ms: int | None = None
        self._transport_admission_stopped = False
        self._publications: dict[str, _IpcPublication] = {}
        self._ipc_lookups: set[asyncio.Task[OutboxRecord | None]] = set()
        self._stop = asyncio.Event()
        self.exit_committed = False
        self._ipc_stopping = False
        self._quiesce_lock = asyncio.Lock()

    @property
    def identity(self) -> ExecutorIdentity:
        return self._identity()

    def accepting(self, link: Link) -> bool:
        return (
            self._link is link
            and self._handshake_accepted
            and self.state is ConnectionState.ready
            and not self._transport_admission_stopped
        )

    def connection(self) -> Link | None:
        return self._link if self._handshake_accepted else None

    def health(self) -> ExecutorHealth:
        return ExecutorHealth(
            self.identity,
            os.getpid(),
            self.state,
            self.state == ConnectionState.ready,
            now_ms(),
            self.lost_at_ms,
        )

    async def request(
        self, kind: WireMessageKind, payload: JsonObject, *, request_id: str | None = None
    ) -> WireMessage:
        link = self._link
        if link is None:
            raise ConnectionError("executor link unavailable")
        return await link.request(kind, payload, request_id=request_id)

    async def handle(self, request: IpcRequest) -> IpcResponse:
        try:
            if self._ipc_stopping:
                raise ConnectionError("executor IPC is stopping")
            if request.operation == IpcOperation.health:
                return IpcResponse(request.request_id, as_object(self.health()), None)
            if request.operation == IpcOperation.quiesce:
                expected = type_adapter(QuiesceRequest).validate_python(request.payload)
                async with self._quiesce_lock:
                    if expected.expected_identity != self.identity or (
                        expected.expected_process_id != os.getpid()
                    ):
                        raise ValueError("quiesce identity changed; obtain fresh health")
                    self._transport_admission_stopped = True
                    await self._settle_catalogs()
                    quiescence = await self.runner.quiesce()
                    pending_transfers = self.runner._transfers
                    if pending_transfers:
                        quiescence = replace(
                            quiescence,
                            uncertain_operation_count=quiescence.uncertain_operation_count
                            + pending_transfers,
                        )
                    if expected.expected_identity != self.identity or (
                        expected.expected_process_id != os.getpid()
                    ):
                        raise ValueError(
                            "quiesce identity changed while draining; obtain fresh health"
                        )
                    # Include reserved IPC tasks even before source_work gets CPU.
                    ipc_count = sum(not row.task.done() for row in self._publications.values())
                    ipc_count += len(self._ipc_lookups)
                    if ipc_count:
                        quiescence = replace(
                            quiescence,
                            uncertain_operation_count=max(
                                quiescence.uncertain_operation_count, ipc_count
                            ),
                        )
                    exit_committed = quiescence.ready and expected.exit_when_ready
                    if exit_committed:
                        self.exit_committed = True
                        self._stop.set()
                    result = QuiescenceReceipt(
                        self.identity, os.getpid(), quiescence, exit_committed
                    )
                    return IpcResponse(request.request_id, as_object(result), None)
            child = self.runner.authenticate(request.child_id, request.capability)
            if request.operation == IpcOperation.read:
                if self.state != ConnectionState.ready:
                    raise ConnectionError("VPS unavailable; reads never use journal")
                response = await self.request(
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

            deadline = time.monotonic() + CLI_RESULT_TIMEOUT_SECONDS
            if request.response_deadline is not None:
                if not math.isfinite(request.response_deadline):
                    raise ValueError("invalid IPC response deadline")
                deadline = min(deadline, request.response_deadline)
            if request.operation is IpcOperation.retry:
                retry = type_adapter(IpcRetryPayload).validate_python(request.payload)
                safe_component(retry.submission_id)
                publication = self._publications.get(retry.submission_id)
                if publication is not None:
                    if publication.write.child != child.reference:
                        raise PermissionError("submission belongs to another executor child")
                    return await self._observe_publication(
                        request.request_id, retry.submission_id, publication, deadline
                    )
                lookup = asyncio.create_task(
                    asyncio.to_thread(self.spool.lookup, retry.submission_id)
                )
                self._ipc_lookups.add(lookup)

                def looked_up(task: asyncio.Task[OutboxRecord | None]) -> None:
                    self._ipc_lookups.discard(task)
                    if not task.cancelled():
                        task.exception()

                lookup.add_done_callback(looked_up)
                try:
                    row = await asyncio.wait_for(
                        asyncio.shield(lookup), max(0, deadline - time.monotonic())
                    )
                except (TimeoutError, OSError):
                    return self._unconfirmed(request.request_id, retry.submission_id, "unknown")
                if row is not None:
                    original = type_adapter(CliWrite).validate_python(row.envelope.payload)
                    if (
                        original.child != child.reference
                        or row.envelope.kind != EnvelopeKind.cli_write
                    ):
                        raise PermissionError("submission belongs to another executor child")
                return self._publication_status(request.request_id, retry.submission_id, row)

            if request.operation is not IpcOperation.write:
                raise ValueError("unsupported executor IPC operation")
            incoming = type_adapter(IpcWritePayload).validate_python(request.payload)
            safe_component(request.request_id)
            write = CliWrite(
                incoming.operation,
                as_object(incoming.arguments),
                child.reference,
                child.active_turn,
            )
            publication = self._publications.get(request.request_id)
            if publication is not None:
                self._match_publication(publication.write, write)
            else:
                if self.runner.admission_stopped:
                    raise PermissionError("executor admission stopped")

                async def publish_write() -> OutboxRecord:
                    assert publication is not None
                    previous = await asyncio.to_thread(self.spool.lookup, request.request_id)
                    if previous is not None:
                        if previous.envelope.kind != EnvelopeKind.cli_write:
                            raise _IpcConflict("submission identity conflicts with journal")
                        self._match_publication(
                            type_adapter(CliWrite).validate_python(previous.envelope.payload), write
                        )
                        publication.admitted = True
                        return previous
                    publication.admitted = True
                    try:
                        envelope = await asyncio.to_thread(
                            self.spool.add,
                            OutboxEntry(
                                child.reference.conversation_id,
                                EnvelopeKind.cli_write,
                                as_object(write),
                                request.request_id,
                            ),
                            ticket_id=child.ticket_id,
                        )
                    except (OSError, ValueError, sqlite3.Error):
                        self.runner.record_lost_output(child.reference)
                        raise
                    return OutboxRecord(envelope)

                task = asyncio.create_task(
                    self.runner.source_work(child.reference, publish_write, new_work=True)
                )
                publication = _IpcPublication(write, task)
                # Reserve before the first await, including a blocked journal lookup.
                self._publications[request.request_id] = publication
                task.add_done_callback(
                    lambda _: self._release_publication(request.request_id, publication)
                )
            return await self._observe_publication(
                request.request_id, request.request_id, publication, deadline
            )
        except (ValueError, PermissionError, ConnectionError) as error:
            code = "validation"
            if isinstance(error, PermissionError):
                code = "agent_forbidden"
            elif isinstance(error, ConnectionError):
                code = "connection"
            return IpcResponse(
                request.request_id, None, {"code": code, "message": str(error), "detail": {}}
            )

    @staticmethod
    def _match_publication(original: CliWrite, incoming: CliWrite) -> None:
        # Retain the original turn and snapshots. Current turn state is not replay input.
        if (original.child, original.operation, original.arguments) != (
            incoming.child,
            incoming.operation,
            incoming.arguments,
        ):
            raise _IpcConflict("submission identity reused with different content")

    @staticmethod
    def _unconfirmed(
        request_id: str,
        submission_id: str,
        publication: Literal["unknown", "preparing", "queued", "acknowledged"],
    ) -> IpcResponse:
        return IpcResponse(
            request_id,
            None,
            None,
            unconfirmed=UnconfirmedCliResult(request_id, submission_id, publication),
        )

    def _publication_status(
        self, request_id: str, submission_id: str, row: OutboxRecord | None
    ) -> IpcResponse:
        if row is None:
            return self._unconfirmed(request_id, submission_id, "unknown")
        if row.pending and request_id != submission_id:
            return self._unconfirmed(request_id, submission_id, "queued")
        if row.pending:
            # Keep the existing durable queue contract for callers that already use it.
            return IpcResponse(request_id, None, None, QueuedCliResult(submission_id))
        assert row.receipt is not None
        return self._receipt_response(request_id, row.receipt)

    def _release_publication(self, submission_id: str, publication: _IpcPublication) -> None:
        if not publication.task.done():
            return
        if not publication.task.cancelled():
            publication.task.exception()  # Consume failure even after every caller disconnects.
        if publication.observers == 0 and self._publications.get(submission_id) is publication:
            del self._publications[submission_id]

    async def _observe_publication(
        self, request_id: str, submission_id: str, publication: _IpcPublication, deadline: float
    ) -> IpcResponse:
        publication.observers += 1
        confirmation = asyncio.create_task(
            self.spool.wait_confirmed(submission_id, max(0, deadline - time.monotonic()))
        )
        try:
            done, _ = await asyncio.wait(
                (publication.task, confirmation),
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if confirmation in done and not publication.admitted:
                await asyncio.wait((publication.task,), timeout=max(0, deadline - time.monotonic()))
            if confirmation.done() and confirmation.result() is not None and publication.admitted:
                receipt = confirmation.result()
                assert receipt is not None
                return self._receipt_response(request_id, receipt)
            if publication.task.cancelled():
                return self._unconfirmed(request_id, submission_id, "unknown")
            if publication.task.done():
                error = publication.task.exception()
                if error is not None:
                    if not publication.admitted or isinstance(error, _IpcConflict):
                        raise error
                    return self._unconfirmed(request_id, submission_id, "unknown")
                receipt = await confirmation
                if receipt is not None:
                    return self._receipt_response(request_id, receipt)
                return self._publication_status(
                    request_id, submission_id, publication.task.result()
                )
            return self._unconfirmed(request_id, submission_id, "preparing")
        finally:
            confirmation.cancel()
            await asyncio.gather(confirmation, return_exceptions=True)
            publication.observers -= 1
            self._release_publication(submission_id, publication)

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
            self._ipc_stopping = True
            self.runner.admission_stopped = True
            socket_path.unlink(missing_ok=True)
            for publication in tuple(self._publications.values()):
                await wait_for_settlement(publication.task)
            for lookup in tuple(self._ipc_lookups):
                await wait_for_settlement(lookup)
