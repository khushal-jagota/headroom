"""Compose VPS executor owners and adapt app control and file transport."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, cast

from starlette.types import ASGIApp

from planner.conversation.message_content import MessageFile
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import Clock
from planner.core.contracts import PrincipalKind
from planner.core.errors import PlannerError
from planner.execution_identity import ExecutorIdentity
from planner.executor.catalog_relay import VpsCatalogRelay
from planner.executor.files import file_digest
from planner.executor.link import Link, accept_link
from planner.executor.link_files import GenerationTransfers, settled_io
from planner.executor.protocol import (
    LIVE_BACKEND_EVENT_METHODS,
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
        self._last_contact_at_ms: int | None = None
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
            "last_contact_at_ms": self.last_contact_at_ms,
            "connection_state": "ready"
            if self.runs.ready
            else "disconnected"
            if self.runs.link is None
            else "reconciling",
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
            for kind, handler in {
                WireMessageKind.reconciliation_ready: self._reconciliation_ready,
                WireMessageKind.live_backend_event: self._live_backend_event,
                WireMessageKind.envelope: self._envelope,
                WireMessageKind.refuse_envelope: self._refuse_envelope,
                WireMessageKind.command_outcome: self._command_outcome,
                WireMessageKind.read_request: self._read_request,
                WireMessageKind.put_file: self._put_file,
                WireMessageKind.get_file: self._get_file,
            }.items():
                link.on(kind, partial(self._handle, writer=link, handler=handler))
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
                    await self.runs.link_down(owned)
                await self._close_transport(identity)

    async def _handle(
        self,
        message: WireMessage,
        writer: Link,
        handler: Callable[[WireMessage, Link], Awaitable[WireMessage | None]],
    ) -> WireMessage | None:
        if self.runs.link is not writer or self.runs.identity != writer.identity:
            return None
        try:
            response = await handler(message, writer)
        except (sqlite3.OperationalError, OSError):
            writer.close()
            return None
        except (ValueError, PlannerError, KeyError, TypeError) as error:
            response = WireMessage(
                WireMessageKind.error,
                message.request_id,
                error.to_payload()
                if isinstance(error, PlannerError)
                else {"error": {"code": "validation", "message": str(error)}},
            )
        # Link closes this original writer if an unexpected failure escapes.
        # Committed work recovers through replay; never answer on a replacement.
        if self.runs.link is writer and self.runs.identity == writer.identity:
            return response
        return None

    async def _reconciliation_ready(self, message: WireMessage, writer: Link) -> None:
        await self.runs.link_up(writer, message.request_id)
        self.catalog.connected()

    async def _live_backend_event(self, message: WireMessage, writer: Link) -> None:
        event = type_adapter(BackendEvent).validate_python(message.payload)
        if event.method not in LIVE_BACKEND_EVENT_METHODS:
            raise ValueError("live frame must not carry a recorded event")
        if event.child.executor_incarnation != writer.identity.executor_incarnation:
            raise ValueError("live event incarnation conflicts with connection")
        child = self.runs.children.get(event.child.child_id)
        if child is not None and child.reference == event.child and child.event_sink is not None:
            await child.event_sink.report(event.turn, decode_event_arguments(event))

    async def _envelope(self, message: WireMessage, writer: Link) -> WireMessage:
        envelope = type_adapter(QueuedEnvelope).validate_python(message.payload)
        try:
            receipt = await self.inbox.accept(envelope, writer.identity)
            return WireMessage(WireMessageKind.receipt, message.request_id, as_object(receipt))
        except MissingFiles as missing:
            return WireMessage(
                WireMessageKind.missing_files,
                message.request_id,
                {"files": encode_value(missing.manifests)},
            )

    async def _refuse_envelope(self, message: WireMessage, writer: Link) -> WireMessage:
        receipt = await self.inbox.refuse_item(
            type_adapter(EnvelopeRefusal).validate_python(message.payload),
            writer.identity,
            message.request_id,
            self.transfers[writer.identity],
        )
        return WireMessage(WireMessageKind.receipt, message.request_id, as_object(receipt))

    async def _command_outcome(self, message: WireMessage, writer: Link) -> None:
        outcome = type_adapter(CommandOutcome).validate_python(message.payload)
        if message.request_id != outcome.command_id:
            raise ValueError("command result ID conflicts with reply")
        self.runs._resolve_command(outcome)

    async def _read_request(self, message: WireMessage, writer: Link) -> WireMessage:
        read = type_adapter(LiveReadRequest).validate_python(message.payload)
        return WireMessage(
            WireMessageKind.read_response, message.request_id, await self._read(read)
        )

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

    async def _put_file(self, message: WireMessage, writer: Link) -> WireMessage:
        service = self.transfers[writer.identity]
        upload = type_adapter(FileUploadRequest).validate_python(message.payload)
        await settled_io(self.inbox.authorize_upload, upload)
        slot = service.create_upload(upload.manifest)
        service.uploads[slot.transfer_id] = message.request_id, upload
        return WireMessage(WireMessageKind.file_transfer, message.request_id, as_object(slot))

    async def _get_file(self, message: WireMessage, writer: Link) -> WireMessage:
        identity = writer.identity
        service = self.transfers[identity]
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
        return WireMessage(WireMessageKind.file_transfer, message.request_id, as_object(slot))

    async def store_upload(self, identity: ExecutorIdentity, transfer_id: str) -> None:
        service = self.transfers.get(identity)
        pending = service.uploads.pop(transfer_id, None) if service is not None else None
        if service is None or pending is None:
            raise ValueError("unknown file upload")
        _, upload = pending
        try:
            await settled_io(self.inbox.authorize_upload, upload)
            source = service.upload_path(transfer_id)
            await service.run_guarded(self.inbox.store_upload, upload, source)
        finally:
            service.release(transfer_id)

    async def _close_transport(self, identity: ExecutorIdentity) -> None:
        # Replacement and old-session cleanup race; take ownership before yielding.
        service = self.transfers.pop(identity, None)
        if service is not None:
            await service.close()
