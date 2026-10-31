"""Compose VPS executor owners and adapt app control and file transport."""

from __future__ import annotations

import asyncio
import sqlite3
from functools import partial
from typing import Any, cast

from starlette.types import ASGIApp

from planner.conversation.message_content import MessageFile
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import Clock
from planner.core.contracts import PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.execution_identity import (
    ChildReference,
    ExecutorIdentity,
)
from planner.executor.catalog_relay import VpsCatalogRelay
from planner.executor.clock import now_ms
from planner.executor.files import file_digest
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
    FileManifest,
    FileScope,
    FileSnapshot,
    FileUploadRequest,
    JsonObject,
    LiveReadRequest,
    QueuedEnvelope,
    Steer,
    WireMessage,
    WireMessageKind,
    WritePrompt,
    as_object,
    decode_event_arguments,
    encode_value,
    envelope_child,
    type_adapter,
)
from planner.executor.vps_inbox import MacInbox, MissingFiles
from planner.executor.vps_runs import MacRuns


class VpsExecutor:
    """Compose VPS owners and adapt app transport frames to their typed inputs."""

    def __init__(self, db_path: str, clock: Clock, *, machine_id: str = "macos") -> None:
        self.db_path = db_path
        self.runs = MacRuns(db_path)
        self.inbox = MacInbox(db_path, clock, self.runs)
        self.catalog = VpsCatalogRelay(self.runs, machine_id=machine_id)
        self.conversations: SqliteProcessConversationSystem
        self.message_files = ConversationMessageFiles(db_path)
        self.app: ASGIApp
        self.process_id: int | None = None
        self.observed_at_ms: int | None = None
        self._last_contact_at_ms: int | None = None
        self.lost_at_ms: int | None = None
        self.transfers: dict[ExecutorIdentity, GenerationTransfers] = {}
        self._transport_lock = asyncio.Lock()

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
        for identity in tuple(self.transfers):
            await self._close_transport(identity)
        if writer is not None:
            await writer.wait_closed()

    @property
    def last_contact_at_ms(self) -> int | None:
        link = self.runs.link
        return link.last_received_at_ms if link is not None else self._last_contact_at_ms

    def health(self) -> ExecutorHealthRead:
        return {
            "identity": None if self.runs.identity is None else as_object(self.runs.identity),
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

    async def session(self, reader: ControlMessageReader, writer: ControlMessageWriter) -> None:
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
            self.transfers[identity] = GenerationTransfers(
                identity, lambda: self.runs.identity == identity and self.runs.link is link
            )
            for kind in (
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
        try:
            response_kind = WireMessageKind.error
            result: JsonObject
            if message.kind is WireMessageKind.reconciliation_ready:
                await self.runs.link_up(writer, message.request_id)
                self.catalog.connected()
                return
            elif message.kind is WireMessageKind.live_backend_event:
                event = type_adapter(BackendEvent).validate_python(message.payload)
                if event.method not in LIVE_BACKEND_EVENT_METHODS:
                    raise ValueError("live frame must not carry a recorded event")
                if event.child.executor_incarnation != identity.executor_incarnation:
                    raise ValueError("live event incarnation conflicts with connection")
                child = self.runs.children.get(event.child.child_id)
                if (
                    child is not None
                    and child.reference == event.child
                    and child.event_sink is not None
                ):
                    await child.event_sink.report(event.turn, decode_event_arguments(event))
                return
            elif message.kind is WireMessageKind.envelope:
                envelope = type_adapter(QueuedEnvelope).validate_python(message.payload)
                try:
                    result = as_object(await self.inbox.accept(envelope, identity))
                    response_kind = WireMessageKind.receipt
                except MissingFiles as missing:
                    result = {"files": encode_value(missing.manifests)}
                    response_kind = WireMessageKind.missing_files
            elif message.kind is WireMessageKind.error:
                result = as_object(await self._refuse_upload_item(message, identity))
                response_kind = WireMessageKind.receipt
            elif message.kind is WireMessageKind.command_outcome:
                outcome = type_adapter(CommandOutcome).validate_python(message.payload)
                if message.request_id != outcome.command_id:
                    raise ValueError("command result ID conflicts with reply")
                self.runs._resolve_command(outcome)
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
        child = type_adapter(ChildReference).validate_python(envelope_child(envelope.payload))
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
        service = self.transfers[identity]
        for transfer_id, (request_id, upload) in tuple(service.uploads.items()):
            if request_id == message.request_id:
                if upload.child != child or upload.manifest not in envelope.file_dependencies:
                    raise ValueError("upload refusal does not belong to this item")
                service.uploads.pop(transfer_id)
                service.release(transfer_id)
        lock = self.inbox._conversation_locks.setdefault(envelope.conversation_id, asyncio.Lock())
        async with lock:
            return await self.inbox._record_refusal(
                envelope, identity, PlannerError(ErrorCode.validation, rejected.error_message)
            )

    async def _file(
        self, message: WireMessage, identity: ExecutorIdentity
    ) -> tuple[JsonObject, WireMessageKind]:
        service = self.transfers.get(identity)
        if service is None:
            raise ValueError("file generation unavailable")
        if message.kind is WireMessageKind.put_file:
            upload = type_adapter(FileUploadRequest).validate_python(message.payload)
            await settled_io(self.inbox.authorize_upload, upload)
            await settled_io(self.runs.store.verify_connection, identity)
            slot = service.create_upload(upload.manifest)
            service.uploads[slot.transfer_id] = message.request_id, upload
            return as_object(slot), WireMessageKind.file_transfer
        request = type_adapter(FileDownloadRequest).validate_python(message.payload)
        pending = self.runs.pending_commands.get(request.command_id)
        if pending is None or pending.future.done():
            raise ValueError("input command is no longer pending")
        command = pending.command
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
        digest, byte_count = await settled_io(file_digest, path)
        snapshot = FileSnapshot(
            FileManifest(
                FileScope.conversation,
                command.child.conversation_id,
                path.name,
                digest,
                byte_count,
            ),
            path,
        )
        slot = await service.create_download_async(snapshot)
        await settled_io(self.runs.store.verify_connection, identity)
        return as_object(slot), WireMessageKind.file_transfer

    async def store_upload(self, identity: ExecutorIdentity, transfer_id: str) -> None:
        service = self.transfers.get(identity)
        pending = service.uploads.pop(transfer_id, None) if service is not None else None
        if service is None or pending is None:
            raise ValueError("unknown file upload")
        _, upload = pending
        try:
            await settled_io(self.runs.store.verify_connection, identity)
            await settled_io(self.inbox.authorize_upload, upload)
            source = service.upload_path(transfer_id)
            await service.run_guarded(self.inbox.store_upload, upload, source)
            await settled_io(self.runs.store.verify_connection, identity)
        finally:
            service.release(transfer_id)

    async def _close_transport(self, identity: ExecutorIdentity) -> None:
        # Replacement and old-session cleanup race; take ownership before yielding.
        service = self.transfers.pop(identity, None)
        if service is not None:
            await service.close()
