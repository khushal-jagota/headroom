"""Mac-dialed executor service: native children, durable replay and owner-only IPC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import aiohttp
from pydantic import TypeAdapter

from planner.conversation.backends.claude_agent_sdk import ClaudeAgentSdkBackendChildFactory
from planner.conversation.backends.codex_app_server import CodexAppServerBackendChildFactory
from planner.conversation.backends.contracts import AdmissionRefused, BackendChildFactory
from planner.conversation.backends.hermes_acp import HermesAcpBackendChildFactory
from planner.conversation.contracts import (
    ConversationBackendKey,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.message_content import MessageFile
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.contracts import (
    CatalogRequest,
    CatalogResponse,
    CatalogUnavailable,
    CorrectiveAction,
    ProviderCatalogObserver,
    ValidationReason,
)
from planner.conversation.production_backends import production_backend_launches
from planner.executor.app_transport import (
    ControlWebSocket,
    ExecutorAppClient,
    create_client_session,
    validate_server_url,
)
from planner.executor.codec import encode_value
from planner.executor.configuration import (
    CATALOG_CANCEL_TIMEOUT_SECONDS,
    CATALOG_TIMEOUT_SECONDS,
    CLI_RESULT_TIMEOUT_SECONDS,
    MAX_CATALOG_OBSERVATIONS,
    MAX_ENVELOPE_BYTES,
    MAX_TRANSFERS,
    PROTOCOL_VERSION,
    RECONNECT_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    SPOOL_FORMAT_VERSION,
    ExecutorPaths,
)
from planner.executor.contracts import (
    AcceptanceReceipt,
    BackendEvent,
    ChildCommand,
    ChildReference,
    CliWrite,
    ConnectionFailureOrigin,
    ConnectionPhase,
    ConnectionState,
    ControlMessageReader,
    ControlMessageWriter,
    EnvelopeKind,
    EnvelopeRefusal,
    ExecutorHealth,
    ExecutorIdentity,
    FileDownloadRequest,
    FileScope,
    FileSnapshot,
    FileTransfer,
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
    QueuedEnvelope,
    QuiescenceReceipt,
    QuiesceRequest,
    ReceiptDisposition,
    ReconnectAsk,
    ReconnectResult,
    Steer,
    UnconfirmedCliResult,
    WireMessage,
    WireMessageKind,
    WithdrawCommands,
    WithdrawResult,
    WritePrompt,
)
from planner.executor.diagnostics import connection_diagnostics
from planner.executor.files import (
    contained_path,
    copy_verified,
    directory_descriptor,
    safe_component,
)
from planner.executor.mac_outbox import MacOutbox
from planner.executor.runtime import NativeChildRegistry, now_ms
from planner.executor.transfers import settled_io, wait_for_settlement
from planner.executor.wire import (
    ProtocolRefused,
    encode_frame,
    parse_identity,
    read_typed_frame,
)
from planner.tickets.contracts import TicketLocale


def as_object(value: object) -> JsonObject:
    return cast(JsonObject, encode_value(value))


class _IpcConflict(ValueError):
    """A conflicting reuse is refused before this attempt can publish."""


@dataclass
class _IpcPublication:
    write: CliWrite
    task: asyncio.Task[OutboxRecord]
    observers: int = 0
    admitted: bool = False


class MacExecutor:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        revision: str,
        executor_id: str,
        panels_server_url: str,
        session_factory: Callable[[], aiohttp.ClientSession] = create_client_session,
        registry: NativeChildRegistry | None = None,
        catalog_observer: ProviderCatalogObserver | None = None,
    ) -> None:
        self.paths = paths
        self.server_url = validate_server_url(panels_server_url)
        self.executor_id = executor_id
        self._session_factory = session_factory
        self._app_client: ExecutorAppClient | None = None
        self._transfers = 0
        self._transfer_capacity = asyncio.Semaphore(MAX_TRANSFERS)
        self._transport_admission_stopped = False
        self.spool = MacOutbox(paths.spool)
        self.identity = ExecutorIdentity(
            PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()), 0
        )
        self._handshake_accepted = False
        self.state = ConnectionState.disconnected
        self.lost_at_ms: int | None = None
        self._writer: ControlMessageWriter | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[WireMessage]] = {}
        self._publications: dict[str, _IpcPublication] = {}
        self._ipc_lookups: set[asyncio.Task[OutboxRecord | None]] = set()
        self._stop = asyncio.Event()
        self.exit_committed = False
        self._ipc_stopping = False
        self._command_tasks: set[asyncio.Task[None]] = set()
        self._quiesce_lock = asyncio.Lock()
        self._catalog_observer = catalog_observer
        self._catalog_tasks: dict[
            str, tuple[CatalogRequest, ControlMessageWriter, asyncio.Task[None]]
        ] = {}
        self.registry = registry or NativeChildRegistry(
            paths=paths,
            incarnation=self.identity.executor_incarnation,
            spool=self.spool,
            factory=self._factory(panels_server_url),
            message_files=ConversationMessageFiles(paths.root / "artifacts" / "unused-db-path"),
        )

    def _factory(
        self, server_url: str
    ) -> Callable[[ResolvedConversationStart, str, ChildReference], BackendChildFactory]:
        def factory(
            resolved: ResolvedConversationStart, capability: str, reference: ChildReference
        ) -> BackendChildFactory:
            launches = production_backend_launches(panels_server_url=server_url)
            identity = dict(
                resolved.role_materials.identity_environment_variables
                if resolved.role_materials is not None
                else ()
            )
            ticket_id = identity.get("PLAN_TICKET_ID", resolved.conversation_id)
            artifact_root = self.paths.artifacts / safe_component(ticket_id)
            artifact_root.mkdir(parents=True, exist_ok=True)
            # These override transport facts only. HOME/provider state remain inherited.
            overrides = (
                ("PLAN_EXECUTOR_ROOT", str(self.paths.root)),
                ("PLAN_EXECUTOR_CHILD_ID", reference.child_id),
                ("PLAN_EXECUTOR_CAPABILITY", capability),
                ("PLAN_TICKET_FILES_DIR", str(artifact_root)),
            )
            if resolved.backend_key == ConversationBackendKey.codex:
                return CodexAppServerBackendChildFactory(
                    replace(
                        launches.codex,
                        environment_overrides=launches.codex.environment_overrides + overrides,
                    )
                )
            if resolved.backend_key == ConversationBackendKey.hermes:
                return HermesAcpBackendChildFactory(
                    replace(
                        launches.hermes,
                        environment_overrides=launches.hermes.environment_overrides + overrides,
                    )
                )
            return ClaudeAgentSdkBackendChildFactory(
                replace(
                    launches.claude,
                    environment_overrides=launches.claude.environment_overrides + overrides,
                ),
            )

        return factory

    def health(self) -> ExecutorHealth:
        return ExecutorHealth(
            self.identity,
            os.getpid(),
            self.state,
            self.state == ConnectionState.ready,
            now_ms(),
            self.lost_at_ms,
        )

    async def _send(self, message: WireMessage) -> None:
        async with self._write_lock:
            writer = self._writer
            if writer is None:
                raise ConnectionError("executor link unavailable")
            await writer.send(message)

    async def _send_live_event(self, event: BackendEvent) -> None:
        # Live text has no receipt or retry. Never carry it into a new generation.
        writer = self._writer
        identity = self.identity
        if writer is None or self.state is not ConnectionState.ready:
            return
        with contextlib.suppress(ConnectionError, OSError):
            async with self._write_lock:
                if self._writer is writer and self.identity == identity:
                    await writer.send(
                        WireMessage(
                            WireMessageKind.live_backend_event, uuid4().hex, as_object(event)
                        )
                    )

    async def _request(
        self, kind: WireMessageKind, payload: JsonObject, *, request_id: str | None = None
    ) -> WireMessage:
        request_id = request_id or uuid4().hex
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        writer = self._writer
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._write_lock:
                    if writer is None or self._writer is not writer:
                        raise ConnectionError("executor link unavailable")
                    await writer.send(WireMessage(kind, request_id, payload))
                response = await future
            if response.kind in (WireMessageKind.error, WireMessageKind.protocol_refused):
                raise ValueError(str(response.payload))
            return response
        except TimeoutError:
            # The operation may have committed. Disconnect this generation so
            # reconciliation recovers its receipt, without retrying it here.
            if writer is not None:
                writer.close()
            raise
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self, reader: ControlMessageReader) -> None:
        while True:
            message = await reader.receive()
            if message.kind in (WireMessageKind.catalog_request, WireMessageKind.catalog_cancel):
                self._catalog_frame(message)
                continue
            if not self._handshake_accepted and message.kind in (
                WireMessageKind.command,
                WireMessageKind.reconnect_ask,
                WireMessageKind.withdraw,
            ):
                raise ProtocolRefused("peer sent work before validated handshake")
            if message.kind == WireMessageKind.health and "clock_probe" in message.payload:
                probe = cast(JsonObject, message.payload["clock_probe"])
                received = now_ms()
                await self._send(
                    WireMessage(
                        WireMessageKind.health,
                        message.request_id,
                        {
                            "vps_sent_at_ms": probe["vps_sent_at_ms"],
                            "mac_received_at_ms": received,
                            "mac_sent_at_ms": now_ms(),
                        },
                    )
                )
            elif message.kind is WireMessageKind.withdraw:
                request = TypeAdapter(WithdrawCommands).validate_python(message.payload)
                withdrawn = self.registry.withdraw_commands(request)
                await self._send(
                    WireMessage(
                        WireMessageKind.withdraw_result,
                        message.request_id,
                        as_object(WithdrawResult(withdrawn)),
                    )
                )
            elif message.kind == WireMessageKind.command:
                task = asyncio.create_task(self._command(message))
                self._command_tasks.add(task)
                task.add_done_callback(self._command_tasks.discard)
            elif message.kind is WireMessageKind.reconnect_ask:
                ask = TypeAdapter(ReconnectAsk).validate_python(message.payload)
                await self.registry.flush_command_output(ask.command_ids)
                await self._send(
                    WireMessage(
                        WireMessageKind.reconnect_result,
                        message.request_id,
                        as_object(
                            ReconnectResult(
                                self.identity,
                                self.registry.ask(ask.command_ids),
                                self.registry.inventory(),
                            )
                        ),
                    )
                )
            else:
                future = self._pending.get(message.request_id)
                if future is not None and not future.done():
                    if message.kind is WireMessageKind.reconciliation_ready:
                        if not self._handshake_accepted or message.payload != {"ready": True}:
                            raise ProtocolRefused("invalid reconciliation acknowledgment")
                        # Clear outage before readiness becomes visible to readers.
                        self.lost_at_ms = None
                        self.state = ConnectionState.ready
                    future.set_result(message)

    def _catalog_frame(self, message: WireMessage) -> None:
        # Metadata never enters the child registry or the durable journal.
        try:
            request = TypeAdapter(CatalogRequest).validate_json(
                json.dumps(message.payload), strict=True
            )
            context = message.payload["expected_context"]
            assert isinstance(context, dict)
            identity = parse_identity(context.get("executor_identity"))
        except (ValueError, TypeError, KeyError):
            return
        if (
            identity != self.identity
            or request.expected_context.executor_identity != identity
            or not self._handshake_accepted
            or self.state != ConnectionState.ready
            or self._writer is None
        ):
            return
        workspace = request.expected_context.workspace
        if workspace.workspace_path_policy is WorkspacePathPolicy.native_absolute:
            if (
                workspace.workspace_home_relative is not None
                or not workspace.workspace_folder.is_absolute()
            ):
                return
        elif workspace.workspace_home_relative is not None:
            relative = Path(workspace.workspace_home_relative)
            if relative.is_absolute() or ".." in relative.parts:
                return
        existing = self._catalog_tasks.get(message.request_id)
        if message.kind is WireMessageKind.catalog_cancel:
            if existing is not None and existing[0] == request and existing[1] is self._writer:
                # Repeated cancellation must not interrupt physical cleanup.
                if not existing[2].cancelling():
                    existing[2].cancel()
            return
        if existing is not None or self._transport_admission_stopped:
            return
        if len(self._catalog_tasks) >= MAX_CATALOG_OBSERVATIONS:
            # The caller's bounded read yields unresolved evidence. No queue can
            # reserve unbounded tasks while an earlier probe physically settles.
            return
        writer = self._writer
        deadline = asyncio.get_running_loop().time() + CATALOG_TIMEOUT_SECONDS
        task = asyncio.create_task(self._catalog(message, request, identity, writer, deadline))
        self._catalog_tasks[message.request_id] = (request, writer, task)

        def settled(completed: asyncio.Task[None]) -> None:
            current = self._catalog_tasks.get(message.request_id)
            if current is not None and current[2] is completed:
                self._catalog_tasks.pop(message.request_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(settled)

    async def _catalog(
        self,
        message: WireMessage,
        request: CatalogRequest,
        identity: ExecutorIdentity,
        writer: ControlMessageWriter,
        deadline: float,
    ) -> None:
        observer = self._catalog_observer
        if observer is None or (
            hasattr(observer, "executor_identity") and observer.executor_identity != identity
        ):
            from planner.conversation.model_resolution.observer import ProductionCatalogObserver

            observer = ProductionCatalogObserver(
                destination=TicketLocale.macos,
                panels_server_url=self.server_url,
                executor_identity=identity,
            )
            self._catalog_observer = observer
        owner = asyncio.current_task()
        assert owner is not None
        from planner.conversation.model_resolution.contracts import CatalogResult

        async def observe() -> CatalogResult:
            return await observer.observe(
                request.backend_key,
                workspace=request.expected_context.workspace,
                attempt_id=message.request_id,
                deadline=deadline,
            )

        work = asyncio.create_task(observe())
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    result = await asyncio.shield(work)
            except BaseException as error:
                work.cancel()
                await wait_for_settlement(work)
                if isinstance(error, asyncio.CancelledError):
                    raise
                result = CatalogUnavailable(
                    backend_key=request.backend_key,
                    destination=TicketLocale.macos,
                    attempted_context=None,
                    reason=ValidationReason.timeout
                    if isinstance(error, TimeoutError)
                    else ValidationReason.catalog_unavailable,
                    corrective_action=CorrectiveAction.refresh_catalog,
                    detail="The destination catalog observation did not complete.",
                    observed_at=int(time.time()),
                    retained_evidence=None,
                )
            # A catalog result has no authority after cancellation or generation
            # replacement. In particular, never write through self._send here.
            async with asyncio.timeout(CATALOG_CANCEL_TIMEOUT_SECONDS):
                async with self._write_lock:
                    if (
                        self._writer is not writer
                        or self.identity != identity
                        or self.state != ConnectionState.ready
                        or self._transport_admission_stopped
                        or owner.cancelling()
                    ):
                        return
                    await writer.send(
                        WireMessage(
                            WireMessageKind.catalog_response,
                            message.request_id,
                            as_object(CatalogResponse(request.backend_key, identity, result)),
                        )
                    )
        except (ConnectionError, OSError, ValueError, TimeoutError):
            # A read-only failure does not disturb a shared execution connection.
            return
        finally:
            if not work.done():
                work.cancel()
            await wait_for_settlement(work)

    async def _settle_catalogs(self) -> None:
        tasks = tuple(task for _, _, task in self._catalog_tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        for task in tasks:
            await wait_for_settlement(task)

    async def _command(self, message: WireMessage) -> None:
        command = TypeAdapter(ChildCommand).validate_python(message.payload)
        try:
            outcome = await self.registry.execute_command(command)
        except (OSError, ValueError):
            # Required output may not have reached disk. Reconnect retries that
            # publication before answering the ask; it never repeats the provider call.
            if self._writer is not None:
                self._writer.close()
            return
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            await self._send(
                WireMessage(WireMessageKind.command_outcome, message.request_id, as_object(outcome))
            )

    async def _prepare_inputs(self, command: ChildCommand) -> None:
        args = command.arguments
        if not isinstance(args, (WritePrompt, Steer)):
            return
        seen: set[str] = set()
        for content in (args.content, args.sender_content or ()):
            for piece in content:
                if isinstance(piece, MessageFile) and piece.stored_file_id not in seen:
                    seen.add(piece.stored_file_id)
                    await self.get_file(command, piece.stored_file_id)

    async def get_file(self, command: ChildCommand, stored_file_id: str) -> None:
        identity, client = self.identity, self._app_client
        self._transfers += 1
        temporary = self.paths.run / (".input-" + uuid4().hex)
        try:
            async with self._transfer_capacity:
                response = await self._request(
                    WireMessageKind.get_file,
                    as_object(FileDownloadRequest(command.command_id, stored_file_id)),
                )
                slot = TypeAdapter(FileTransfer).validate_python(response.payload)
                manifest = slot.manifest
                if (
                    response.kind is not WireMessageKind.file_transfer
                    or slot.direction != "download"
                    or client is None
                    or manifest.scope is not FileScope.conversation
                    or manifest.owner_id != command.child.conversation_id
                    or Path(manifest.relative_path).name != stored_file_id
                ):
                    raise ValueError("invalid input transfer")
                root = self.paths.root / "artifacts" / "files" / "conversations"
                target = contained_path(
                    root, f"{safe_component(manifest.owner_id)}/{manifest.relative_path}"
                )
                self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
                await client.download(slot, temporary)
                if identity != self.identity or not self._handshake_accepted:
                    raise ConnectionError("input generation changed")

                def install() -> None:
                    with directory_descriptor(target.parent, create=True) as parent:
                        staged = target.parent / (".tmp-" + uuid4().hex)
                        try:
                            copy_verified(temporary, staged, manifest)
                            os.replace(
                                staged.name, target.name, src_dir_fd=parent, dst_dir_fd=parent
                            )
                            os.fsync(parent)
                        finally:
                            staged.unlink(missing_ok=True)

                await settled_io(install)
        except (ConnectionError, aiohttp.ClientError) as error:
            raise AdmissionRefused("input connection changed before provider invocation") from error
        finally:
            temporary.unlink(missing_ok=True)
            self._transfers -= 1

    async def request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        return await self._request(kind, payload)

    async def put_file(
        self, envelope: QueuedEnvelope, snapshot: FileSnapshot
    ) -> AcceptanceReceipt | None:
        identity, client = self.identity, self._app_client
        child = envelope.payload.get("child")
        if child is None:
            ask = cast(JsonObject, envelope.payload.get("ask", {}))
            child = ask.get("child")
        if child is None:
            raise ValueError("file upload lacks assigned child")
        self._transfers += 1
        try:
            async with self._transfer_capacity:
                request_id = uuid4().hex
                try:
                    response = await self._request(
                        WireMessageKind.put_file,
                        {
                            "manifest": as_object(snapshot.manifest),
                            "child": child,
                        },
                        request_id=request_id,
                    )
                    slot = TypeAdapter(FileTransfer).validate_python(response.payload)
                    if (
                        response.kind is not WireMessageKind.file_transfer
                        or slot.direction != "upload"
                        or slot.manifest != snapshot.manifest
                        or client is None
                    ):
                        raise ValueError("invalid upload transfer")
                    await client.upload(slot, snapshot.local_path)
                except ValueError as error:
                    if identity != self.identity or not self._handshake_accepted:
                        raise ConnectionError("file generation changed") from error
                    # This item cannot use the descriptor. Answer its request and
                    # obtain a durable refusal without disturbing other conversations.
                    refused = await self._request(
                        WireMessageKind.error,
                        as_object(EnvelopeRefusal(envelope, str(error))),
                        request_id=request_id,
                    )
                    if refused.kind is not WireMessageKind.receipt:
                        raise ValueError("expected upload refusal receipt") from error
                    return TypeAdapter(AcceptanceReceipt).validate_python(refused.payload)
                if identity != self.identity or not self._handshake_accepted:
                    raise ConnectionError("file generation changed")
                return None
        finally:
            self._transfers -= 1

    async def _connected(self) -> None:
        phase = ConnectionPhase.app_connect

        async def observe[T](awaitable: Awaitable[T], origin: ConnectionFailureOrigin) -> T:
            try:
                return await awaitable
            except Exception as error:
                connection_diagnostics.report(error, phase=phase, origin=origin)
                raise

        self.identity = replace(
            self.identity, connection_generation=self.identity.connection_generation + 1
        )
        self.state = ConnectionState.reconciling
        self._handshake_accepted = False
        identity = self.identity
        client = ExecutorAppClient(
            self.server_url, self.executor_id, identity, self._session_factory
        )
        self._app_client = client
        reader_task: asyncio.Task[None] | None = None
        session_task: asyncio.Task[None] | None = None
        stop_task: asyncio.Task[bool] | None = None
        try:
            async with client.websocket("/api/executor/connect", MAX_ENVELOPE_BYTES) as peer:
                stream = ControlWebSocket(peer)
                self._writer = stream
                phase = ConnectionPhase.hello
                reader_task = asyncio.create_task(
                    observe(self._read_loop(stream), ConnectionFailureOrigin.reader)
                )

                async def session() -> None:
                    nonlocal phase
                    phase = ConnectionPhase.hello
                    response = await self._request(WireMessageKind.hello, as_object(self.health()))
                    if (
                        response.kind is not WireMessageKind.hello_accepted
                        or parse_identity(response.payload) != identity
                    ):
                        raise ValueError("handshake identity changed")
                    self._handshake_accepted = True
                    phase = ConnectionPhase.transport_open
                    opened = await self._request(
                        WireMessageKind.transport_open, {"identity": as_object(identity)}
                    )
                    if opened.kind is not WireMessageKind.transport_opened:
                        raise ValueError("file generation refused")
                    phase = ConnectionPhase.reconnect_ask
                    await self._request(WireMessageKind.reconciliation_ready, as_object(identity))
                    self.lost_at_ms = None
                    self.state = ConnectionState.ready
                    phase = ConnectionPhase.steady_state
                    await self.spool.send_pending(self)

                session_task = asyncio.create_task(
                    observe(session(), ConnectionFailureOrigin.session)
                )
                stop_task = asyncio.create_task(self._stop.wait())
                done, _ = await asyncio.wait(
                    (reader_task, session_task, stop_task), return_when=asyncio.FIRST_COMPLETED
                )
                for done_task in done:
                    await done_task
        except Exception as error:
            connection_diagnostics.report(
                error, phase=phase, origin=ConnectionFailureOrigin.session
            )
            raise
        finally:
            # Revoke this generation before cleanup awaits. Native commands stay
            # under registry ownership and retain outcomes for reconciliation.
            self._handshake_accepted = False
            self.state = ConnectionState.disconnected
            if self.lost_at_ms is None:
                self.lost_at_ms = now_ms()
            writer, self._writer = self._writer, None
            self._app_client = None
            if writer is not None:
                writer.close()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("executor link lost"))
            tasks = [task for task in (reader_task, session_task, stop_task) if task is not None]
            await self._settle_catalogs()
            for cleanup_task in tasks:
                cleanup_task.cancel()
            try:
                await client.close()
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
                if writer is not None:
                    await writer.wait_closed()

    async def handle_ipc(self, request: IpcRequest) -> IpcResponse:
        try:
            if self._ipc_stopping:
                raise ConnectionError("executor IPC is stopping")
            if request.operation == IpcOperation.health:
                return IpcResponse(request.request_id, as_object(self.health()), None)
            if request.operation == IpcOperation.quiesce:
                expected = TypeAdapter(QuiesceRequest).validate_python(request.payload)
                async with self._quiesce_lock:
                    if expected.expected_identity != self.identity or (
                        expected.expected_process_id != os.getpid()
                    ):
                        raise ValueError("quiesce identity changed; obtain fresh health")
                    self._transport_admission_stopped = True
                    await self._settle_catalogs()
                    quiescence = await self.registry.quiesce()
                    pending_transfers = self._transfers
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
            child = self.registry.authenticate(request.child_id, request.capability)
            if request.operation == IpcOperation.read:
                if self.state != ConnectionState.ready:
                    raise ConnectionError("VPS unavailable; reads never use journal")
                response = await self._request(
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
                retry = TypeAdapter(IpcRetryPayload).validate_python(request.payload)
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
                    original = TypeAdapter(CliWrite).validate_python(row.envelope.payload)
                    if (
                        original.child != child.reference
                        or row.envelope.kind != EnvelopeKind.cli_write
                    ):
                        raise PermissionError("submission belongs to another executor child")
                return self._publication_status(request.request_id, retry.submission_id, row)

            if request.operation is not IpcOperation.write:
                raise ValueError("unsupported executor IPC operation")
            incoming = TypeAdapter(IpcWritePayload).validate_python(request.payload)
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
                if self.registry.admission_stopped:
                    raise PermissionError("executor admission stopped")

                async def publish_write() -> OutboxRecord:
                    assert publication is not None
                    previous = await asyncio.to_thread(self.spool.lookup, request.request_id)
                    if previous is not None:
                        if previous.envelope.kind != EnvelopeKind.cli_write:
                            raise _IpcConflict("submission identity conflicts with journal")
                        self._match_publication(
                            TypeAdapter(CliWrite).validate_python(previous.envelope.payload), write
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
                        self.registry.record_lost_output(child.reference)
                        raise
                    return OutboxRecord(envelope)

                task = asyncio.create_task(
                    self.registry.source_work(child.reference, publish_write, new_work=True)
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
            response = await self.handle_ipc(request)
            writer.write(encode_frame(response))
            await writer.drain()
        except (ConnectionError, OSError):
            # Lost response delivery says nothing about an admitted publication.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def run(self) -> None:
        self.registry.prepare_inputs = self._prepare_inputs
        self.registry.send_live_event = self._send_live_event
        self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.paths.ipc_socket.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._ipc_client, path=self.paths.ipc_socket)
        self.paths.ipc_socket.chmod(0o600)
        try:
            async with server:
                while not self._stop.is_set():
                    try:
                        await self._connected()
                    except (
                        OSError,
                        ValueError,
                        ConnectionError,
                        asyncio.IncompleteReadError,
                        aiohttp.ClientError,
                    ) as error:
                        connection_diagnostics.report(error)
                        if not self._stop.is_set():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(
                                    self._stop.wait(), RECONNECT_INTERVAL_SECONDS
                                )
        finally:
            self._ipc_stopping = True
            self.registry.admission_stopped = True
            self.paths.ipc_socket.unlink(missing_ok=True)
            for publication in tuple(self._publications.values()):
                await wait_for_settlement(publication.task)
            for lookup in tuple(self._ipc_lookups):
                await wait_for_settlement(lookup)
            self.spool.close()
