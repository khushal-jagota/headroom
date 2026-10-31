"""Compose VPS executor owners and adapt app control and file transport."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, cast

from starlette.types import ASGIApp

from planner.conversation.message_content import MessageFile
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import Clock
from planner.core.contracts import PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import wait_for_settlement
from planner.execution_identity import (
    ChildReference,
    ExecutorIdentity,
    parse_identity,
)
from planner.executor.catalog_relay import VpsCatalogRelay
from planner.executor.files import snapshot_file
from planner.executor.link import Link, accept_link
from planner.executor.link_files import GenerationTransfers, settled_io
from planner.executor.protocol import (
    LIVE_BACKEND_EVENT_METHODS,
    AcceptanceReceipt,
    BackendEvent,
    CommandOutcome,
    ControlMessageReader,
    ControlMessageWriter,
    EnvelopeRefusal,
    ExecutorHealth,
    ExecutorHealthRead,
    FileDownloadRequest,
    FileScope,
    FileUploadRequest,
    JsonObject,
    LiveReadRequest,
    QueuedEnvelope,
    Steer,
    WireMessage,
    WireMessageKind,
    WritePrompt,
    decode_event_arguments,
    encode_value,
    type_adapter,
)
from planner.executor.vps_inbox import MacInbox, MissingFiles
from planner.executor.vps_runs import MacRuns, now_ms


def _generation_root() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="panels-executor-")


class VpsExecutor:
    """Compose VPS owners and adapt app transport frames to their typed inputs."""

    def __init__(self, db_path: str, clock: Clock) -> None:
        self.db_path = db_path
        self.runs = MacRuns(db_path)
        self.inbox = MacInbox(db_path, clock, self.runs)
        self.catalog = VpsCatalogRelay(self.runs)
        self.conversations: SqliteProcessConversationSystem
        self.message_files = ConversationMessageFiles(db_path)
        self.app: ASGIApp
        self.process_id: int | None = None
        self.observed_at_ms: int | None = None
        self._last_contact_at_ms: int | None = None
        self.lost_at_ms: int | None = None
        self._transports: dict[ExecutorIdentity, GenerationTransfers] = {}
        self._transport_roots: dict[ExecutorIdentity, tempfile.TemporaryDirectory[str]] = {}
        self._upload_transfers: dict[
            tuple[ExecutorIdentity, str], tuple[str, FileUploadRequest]
        ] = {}
        self._transport_lock = asyncio.Lock()
        self._receiver_work: dict[ExecutorIdentity, set[asyncio.Task[object]]] = {}

    def bind_conversations(self, conversations: SqliteProcessConversationSystem) -> None:
        self.conversations = conversations
        self.runs.conversations = conversations
        self.inbox.conversations = conversations
        self.inbox.agent_writes.conversations = conversations

    def configure(self, conversations: SqliteProcessConversationSystem, app: ASGIApp) -> None:
        self.bind_conversations(conversations)
        self.app = app

    async def close(self) -> None:
        writer = self.runs.link
        self._last_contact_at_ms = self.last_contact_at_ms
        if writer is not None:
            writer.close()
            await self.runs.link_down(writer)
        await self.runs.close()
        await self.catalog.close()
        for identity in tuple(self._transports):
            await self._close_transport(identity)
        if writer is not None:
            await writer.wait_closed()

    @property
    def last_contact_at_ms(self) -> int | None:
        link = self.runs.link
        if link is not None and link.last_received_at_ms is not None:
            return link.last_received_at_ms
        return self._last_contact_at_ms

    @last_contact_at_ms.setter
    def last_contact_at_ms(self, value: int | None) -> None:
        self._last_contact_at_ms = value

    def health(self) -> ExecutorHealthRead:
        return {
            "identity": None if self.runs.identity is None else _object(self.runs.identity),
            "process_id": self.process_id,
            "observed_at_ms": self.observed_at_ms,
            "lost_at_ms": self.lost_at_ms,
            "last_contact_at_ms": self.last_contact_at_ms,
            "last_contact_source": "vps_received_valid_executor_frame",
            "connection_state": "ready"
            if self.runs.ready
            else "disconnected"
            if self.runs.link is None
            else "reconciling",
            "reconciliation_ready": self.runs.ready,
        }

    async def _session(self, reader: ControlMessageReader, writer: ControlMessageWriter) -> None:
        link: Link | None = None
        admitted: Link | None = None

        async def admit(health: ExecutorHealth, candidate: Link) -> ExecutorIdentity:
            nonlocal admitted
            identity = health.identity
            async with self._transport_lock:
                old_identity = self.runs.identity
                await self.runs.admit(candidate)
                admitted = candidate
                self.process_id = health.process_id
                self.observed_at_ms = now_ms()
                self.lost_at_ms = None
                self.catalog.clock_sample = None
                self.last_contact_at_ms = now_ms()
            if old_identity is not None:
                await self._close_transport(old_identity)
            if self.runs.link is not candidate:
                raise ConnectionError("executor generation replaced during admission")
            await self.runs.interrupt_replaced(old_identity)
            return identity

        try:
            link = await accept_link(reader, admit, writer=writer)
            identity = link.identity
            if self.runs.link is not link:
                return
            for kind in (
                WireMessageKind.transport_open,
                WireMessageKind.reconciliation_ready,
                WireMessageKind.live_backend_event,
                WireMessageKind.envelope,
                WireMessageKind.error,
                WireMessageKind.command_outcome,
                WireMessageKind.read_request,
                WireMessageKind.put_file,
                WireMessageKind.get_file,
            ):
                link.on(kind, partial(self._handle, identity=identity, writer=link))
            probe = asyncio.create_task(self.catalog._measure_clock())
            self.catalog._tasks.add(probe)
            probe.add_done_callback(self.catalog._tasks.discard)
            await link.closed()
        except (ConnectionError, OSError, ValueError, TimeoutError, PlannerError):
            pass
        finally:
            owned = link or admitted
            if owned is not None:
                identity = owned.identity
                owned.close()
                if self.runs.link is owned:
                    self._last_contact_at_ms = self.last_contact_at_ms
                    self.lost_at_ms = now_ms()
                    await self.runs.link_down(owned)
                await self._close_transport(identity)

    async def _handle(
        self,
        message: WireMessage,
        identity: ExecutorIdentity,
        writer: Link,
    ) -> None:
        try:
            await self._handle_message(
                message,
                identity,
                writer,
            )
        except Exception:
            # An unanswered operation may already have committed. Close only
            # its original generation and recover through ordinary replay.
            writer.close()

    async def _handle_message(
        self,
        message: WireMessage,
        identity: ExecutorIdentity,
        writer: Link,
    ) -> None:
        if self.runs.link is not writer or self.runs.identity != identity:
            return
        self.last_contact_at_ms = now_ms()
        try:
            response_kind = WireMessageKind.error
            result: JsonObject
            if message.kind is WireMessageKind.transport_open:
                async with self._transport_lock:
                    if (
                        identity in self._transports
                        or parse_identity(message.payload.get("identity")) != identity
                    ):
                        raise ValueError("duplicate or stale transport generation")
                    root = _generation_root()
                    root_path = Path(root.name)
                    service = GenerationTransfers(
                        identity,
                        root_path / "s",
                        lambda: self.runs.identity == identity and self.runs.link is writer,
                    )
                    try:
                        await settled_io(self.runs.store.verify_connection, identity)
                    except BaseException:
                        await service.close()
                        root.cleanup()
                        raise
                    self._transports[identity] = service
                    self._transport_roots[identity] = root

                    result, response_kind = (
                        {},
                        WireMessageKind.transport_opened,
                    )
            elif message.kind is WireMessageKind.reconciliation_ready:
                await self.runs.link_up(writer, message.request_id)
                return
            elif message.kind is WireMessageKind.live_backend_event:
                event = type_adapter(BackendEvent).validate_python(message.payload)
                if event.method not in LIVE_BACKEND_EVENT_METHODS:
                    raise ValueError("live frame must not carry a recorded event")
                if event.child.executor_incarnation != identity.executor_incarnation:
                    raise ValueError("live event incarnation conflicts with connection")
                await self.conversations.receive_remote_live_backend_event(
                    event.child, event.turn, decode_event_arguments(event)
                )
                return
            elif message.kind is WireMessageKind.envelope:
                envelope = type_adapter(QueuedEnvelope).validate_python(message.payload)
                try:
                    result = _object(await self.inbox.accept(envelope, identity))
                    response_kind = WireMessageKind.receipt
                except MissingFiles as missing:
                    result = {"files": encode_value(missing.manifests)}
                    response_kind = WireMessageKind.missing_files
            elif message.kind is WireMessageKind.error:
                result = _object(await self._refuse_upload_item(message, identity))
                response_kind = WireMessageKind.receipt
            elif message.kind is WireMessageKind.command_outcome:
                outcome = type_adapter(CommandOutcome).validate_python(message.payload)
                if message.request_id != outcome.command_id:
                    raise ValueError("command result ID conflicts with reply")
                await self.runs._resolve_command(outcome)
                return
            elif message.kind is WireMessageKind.read_request:
                read = type_adapter(LiveReadRequest).validate_python(message.payload)
                result, response_kind = await self._read(read), WireMessageKind.read_response
            elif message.kind in (
                WireMessageKind.put_file,
                WireMessageKind.get_file,
            ):
                result, response_kind = await self._file(message, identity)
            else:
                raise ValueError("unsupported executor frame")
            if self.runs.link is writer and self.runs.identity == identity:
                await writer.tell(response_kind, result, request_id=message.request_id)
        except (sqlite3.OperationalError, OSError):
            writer.close()
        except (ValueError, PlannerError, KeyError, TypeError) as error:
            if self.runs.link is writer:
                response = WireMessage(
                    WireMessageKind.error,
                    message.request_id,
                    error.to_payload()
                    if isinstance(error, PlannerError)
                    else {"error": {"code": "validation", "message": str(error)}},
                )
                await writer.tell(response.kind, response.payload, request_id=response.request_id)

    async def _read(self, read: LiveReadRequest) -> JsonObject:
        if not read.path.startswith(
            (
                "/api/tickets",
                "/api/items",
                "/api/managers",
                "/api/sprint",
                "/api/projects",
                "/api/worker-types",
                "/api/skills",
                "/api/feedback",
                "/files/sprint-items/",
                "/files/managers/",
            )
        ):
            raise ValueError("unsupported executor read")
        import httpx

        from planner.core.contracts import principal_legacy_actor

        principal = await settled_io(self.runs.authority, read.child)
        headers = {"X-Plan-Actor": principal_legacy_actor(principal)}
        if principal.kind is PrincipalKind.ticket:
            headers["X-Plan-Ticket-ID"] = principal.id
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://executor"
        ) as client:
            response = await client.get(read.path, params=cast(Any, read.params), headers=headers)
            try:
                value = response.json()
            except ValueError:
                return {"text": response.text}
            return value if isinstance(value, dict) else {"data": value}

    async def _refuse_upload_item(
        self, message: WireMessage, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        rejected = type_adapter(EnvelopeRefusal).validate_python(message.payload)
        envelope = rejected.envelope
        raw = envelope.payload.get("child")
        if not isinstance(raw, dict):
            ask = envelope.payload.get("ask")
            raw = ask.get("child") if isinstance(ask, dict) else None
        child = type_adapter(ChildReference).validate_python(raw)
        if child.executor_incarnation != identity.executor_incarnation or (
            child.conversation_id != envelope.conversation_id
        ):
            raise ValueError("upload refusal belongs to another child")

        def authorize() -> None:
            principal = self.runs.authority(child)
            for manifest in envelope.file_dependencies:
                self.inbox.store.file_target(manifest)
                if principal.kind is not PrincipalKind.ticket or principal.id != manifest.owner_id:
                    raise ValueError("upload refusal belongs to another Ticket")

        await settled_io(authorize)
        # The request ID identifies only this reservation, not another conversation's bytes.
        for key, (request_id, upload) in tuple(self._upload_transfers.items()):
            if key[0] == identity and request_id == message.request_id:
                if upload.child != child or upload.manifest not in envelope.file_dependencies:
                    raise ValueError("upload refusal does not belong to this item")
                self._upload_transfers.pop(key)
                self._transports[identity].release(key[1])
        lock = self.inbox._conversation_locks.setdefault(envelope.conversation_id, asyncio.Lock())
        async with lock:
            return await self.inbox._record_refusal(
                envelope, identity, PlannerError(ErrorCode.validation, rejected.error_message)
            )

    async def _file(
        self, message: WireMessage, identity: ExecutorIdentity
    ) -> tuple[JsonObject, WireMessageKind]:
        service = self._transports.get(identity)
        if service is None:
            raise ValueError("file generation unavailable")
        if message.kind is WireMessageKind.put_file:
            upload = type_adapter(FileUploadRequest).validate_python(message.payload)
            await settled_io(self.inbox.authorize_upload, upload)
            await settled_io(self.runs.store.verify_connection, identity)
            slot = service.create_upload(upload.manifest)
            self._upload_transfers[identity, slot.transfer_id] = message.request_id, upload
            return _object(slot), WireMessageKind.file_transfer
        request = type_adapter(FileDownloadRequest).validate_python(message.payload)
        pending = self.runs.pending_commands.get(request.command_id)
        if pending is None or pending[1].done():
            raise ValueError("input command is no longer pending")
        command = pending[0]
        if command.child.executor_incarnation != identity.executor_incarnation:
            raise ValueError("input command belongs to another executor")
        args = command.arguments
        allowed = (
            {
                piece.stored_file_id
                for content in (args.content, args.sender_content or ())
                for piece in content
                if isinstance(piece, MessageFile)
            }
            if isinstance(args, (WritePrompt, Steer))
            else set()
        )
        if request.stored_file_id not in allowed or self.runs._initial_command_cancelled(command):
            raise ValueError("input does not belong to this command")
        await settled_io(self.runs.authority, command.child)
        path = await settled_io(
            self.message_files.path_of,
            command.child.conversation_id,
            request.stored_file_id,
        )
        preparation = asyncio.create_task(
            asyncio.to_thread(
                partial(
                    snapshot_file,
                    root=path.parent,
                    relative_path=path.name,
                    scope=FileScope.conversation,
                    owner_id=command.child.conversation_id,
                )
            )
        )
        try:
            snapshot = await asyncio.shield(preparation)
            slot = await service.create_download_async(snapshot)
            await settled_io(self.runs.store.verify_connection, identity)
            return _object(slot), WireMessageKind.file_transfer
        finally:
            await wait_for_settlement(preparation)
            if not preparation.cancelled() and preparation.exception() is None:
                preparation.result().local_path.unlink(missing_ok=True)

    async def store_upload(self, identity: ExecutorIdentity, transfer_id: str) -> None:
        service = self._transports.get(identity)
        pending = self._upload_transfers.pop((identity, transfer_id), None)
        if service is None or pending is None:
            raise ValueError("unknown file upload")
        _, upload = pending
        try:
            await settled_io(self.runs.store.verify_connection, identity)
            await settled_io(self.inbox.authorize_upload, upload)
            source = service.upload_path(transfer_id)
            await self._receive_work(identity, self.inbox.store_upload, upload, source)
            await settled_io(self.runs.store.verify_connection, identity)
        finally:
            service.release(transfer_id)

    async def _receive_work[**P](
        self,
        generation: ExecutorIdentity,
        operation: Callable[P, None],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        # Cancellation cannot terminate a filesystem thread. Keep its source and
        # generation directory alive until the admitted operation has settled.
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        pending = self._receiver_work.setdefault(generation, set())
        pending.add(task)
        try:
            await asyncio.shield(task)
        except BaseException:
            await wait_for_settlement(task)
            raise
        finally:
            pending.discard(task)
            if not pending:
                self._receiver_work.pop(generation, None)

    async def _close_transport(self, identity: ExecutorIdentity) -> None:
        # Take ownership before yielding: replacement and old-session cleanup can
        # arrive together, but only one owns this service and its directory.
        service = self._transports.pop(identity, None)
        root = self._transport_roots.pop(identity, None)
        work = tuple(self._receiver_work.get(identity, ()))

        async def cleanup() -> None:
            try:
                for task in work:
                    await wait_for_settlement(task)
                if service is not None:
                    await service.close()
            finally:
                for key in tuple(self._upload_transfers):
                    if key[0] == identity:
                        self._upload_transfers.pop(key)
                if root is not None:
                    root.cleanup()

        closing = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(closing)
        except BaseException:
            await wait_for_settlement(closing)
            raise


def _object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded
