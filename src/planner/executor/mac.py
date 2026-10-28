"""Mac-dialed executor service: native children, durable replay and owner-only IPC."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, cast
from uuid import uuid4

import aiohttp
from pydantic import TypeAdapter

from planner.conversation.backends.claude_agent_sdk import ClaudeAgentSdkBackendChildFactory
from planner.conversation.backends.codex_app_server import CodexAppServerBackendChildFactory
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.backends.hermes_acp import HermesAcpBackendChildFactory
from planner.conversation.contracts import ConversationBackendKey, ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.production_backends import production_backend_launches
from planner.executor.app_transport import (
    ControlWebSocket,
    ExecutorAppClient,
    create_client_session,
    validate_server_url,
)
from planner.executor.codec import encode_value
from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
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
    ChildCommand,
    ChildReference,
    CliWrite,
    ConnectionFailureOrigin,
    ConnectionPhase,
    ConnectionState,
    EnvelopeKind,
    ExecutorHealth,
    ExecutorIdentity,
    FileSnapshot,
    FileTransfer,
    FrameReader,
    FrameWriter,
    InputTransferCancel,
    InputTransferCancelled,
    InputTransferFailed,
    InputTransferProgress,
    InputTransferRequest,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    IpcRetryPayload,
    IpcWritePayload,
    JournalPublication,
    JsonObject,
    LiveReadRequest,
    QueuedCliResult,
    QueuedEnvelope,
    QuiescenceReceipt,
    QuiesceRequest,
    ReceiptDisposition,
    ReconciliationInventory,
    ReconciliationResult,
    SourceRetirementRequest,
    UnconfirmedCliResult,
    WireMessage,
    WireMessageKind,
)
from planner.executor.diagnostics import connection_diagnostics
from planner.executor.files import IncomingFiles, safe_component, snapshot_ticket
from planner.executor.input_transfers import InputTransferDeadline
from planner.executor.journal import FilesystemSpool
from planner.executor.runtime import NativeChildRegistry, now_ms
from planner.executor.transfers import settled_io, wait_for_settlement
from planner.executor.wire import (
    ProtocolRefused,
    encode_frame,
    parse_identity,
    read_frame,
    read_typed_frame,
)
from planner.previews.forward import MacPreviewForwards


def as_object(value: object) -> JsonObject:
    return cast(JsonObject, encode_value(value))


class _IpcConflict(ValueError):
    """A conflicting reuse is refused before this attempt can publish."""


@dataclass
class _IpcPublication:
    write: CliWrite
    task: asyncio.Task[JournalPublication]
    receipt: asyncio.Future[AcceptanceReceipt]
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
    ) -> None:
        self.paths = paths
        self.server_url = validate_server_url(panels_server_url)
        self.executor_id = executor_id
        self._session_factory = session_factory
        self._app_client: ExecutorAppClient | None = None
        self._transfers = 0
        self._transfer_capacity = asyncio.Semaphore(MAX_TRANSFERS)
        self._transport_admission_stopped = False
        self._replay_lock = asyncio.Lock()
        self.spool = FilesystemSpool(paths.spool)
        self.identity = ExecutorIdentity(
            PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()), 0
        )
        self._handshake_accepted = False
        self._preview_streams: MacPreviewForwards | None = None
        self.state = ConnectionState.disconnected
        self.lost_at_ms: int | None = None
        self._writer: FrameWriter | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[WireMessage]] = {}
        self._receipts: dict[str, asyncio.Future[AcceptanceReceipt]] = {}
        self._publications: dict[str, _IpcPublication] = {}
        self._ipc_lookups: set[asyncio.Task[JournalPublication | None]] = set()
        self._stop = asyncio.Event()
        self.exit_committed = False
        self._ipc_stopping = False
        self.inputs = IncomingFiles(
            paths.root / "artifacts" / "files", paths.run / "input-quarantine"
        )
        self._command_tasks: set[asyncio.Task[None]] = set()
        self._input_transfers: dict[str, InputTransferDeadline] = {}
        self._input_workers: dict[str, asyncio.Task[None]] = {}
        self._transfer_tasks: set[asyncio.Task[None]] = set()
        self._quiesce_lock = asyncio.Lock()
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
                )
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
            writer.write(encode_frame(message))
            await writer.drain()

    async def _request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        request_id = uuid4().hex
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        writer = self._writer
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._write_lock:
                    if writer is None or self._writer is not writer:
                        raise ConnectionError("executor link unavailable")
                    writer.write(encode_frame(WireMessage(kind, request_id, payload)))
                    await writer.drain()
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

    async def _read_loop(self, reader: FrameReader) -> None:
        while True:
            message = await read_frame(reader)
            if message.kind in (
                WireMessageKind.preview_bind,
                WireMessageKind.preview_release,
                WireMessageKind.preview_open,
            ):
                task = asyncio.create_task(self._preview(message))
                self._command_tasks.add(task)
                task.add_done_callback(self._command_tasks.discard)
                continue
            if message.kind is WireMessageKind.input_cancel:
                cancellation = TypeAdapter(InputTransferCancel).validate_python(message.payload)
                state = self._input_transfers.get(message.request_id)
                if state is not None:
                    if (
                        state.request.command_id != cancellation.command_id
                        or state.request.slot.transfer_id != cancellation.transfer_id
                    ):
                        raise ProtocolRefused("input cancellation identity mismatch")
                    state.revoked = True
                    worker = self._input_workers.get(message.request_id)
                    if worker is not None:
                        worker.cancel()
                # Admission is revoked before ACK; physical cleanup remains owned.
                await self._send(
                    WireMessage(
                        WireMessageKind.input_cancelled,
                        message.request_id,
                        as_object(
                            InputTransferCancelled(
                                cancellation.command_id, cancellation.transfer_id
                            )
                        ),
                    )
                )
                continue
            if (
                message.kind == WireMessageKind.file_slot
                and message.request_id not in self._pending
            ):
                request = TypeAdapter(InputTransferRequest).validate_python(message.payload)
                if not self._handshake_accepted or self._transport_admission_stopped:
                    raise ProtocolRefused("input before validated handshake")
                if message.request_id in self._input_transfers:
                    raise ProtocolRefused("duplicate input exchange")
                if len(self._input_transfers) >= MAX_TRANSFERS:
                    await self._send(
                        WireMessage(
                            WireMessageKind.input_failed,
                            message.request_id,
                            as_object(
                                InputTransferFailed(
                                    request.command_id,
                                    request.slot.transfer_id,
                                    "input transfer capacity exceeded",
                                )
                            ),
                        )
                    )
                    continue
                state = InputTransferDeadline(request)
                self._input_transfers[message.request_id] = state
                task = asyncio.create_task(self._input(message, state))
                self._transfer_tasks.add(task)
                task.add_done_callback(self._transfer_tasks.discard)
                continue
            if not self._handshake_accepted and message.kind in (
                WireMessageKind.command,
                WireMessageKind.command_outcome_ack,
                WireMessageKind.source_retire,
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
            elif message.kind == WireMessageKind.source_retire:
                task = asyncio.create_task(self._retire_source(message))
                self._command_tasks.add(task)
                task.add_done_callback(self._command_tasks.discard)
            elif message.kind == WireMessageKind.command:
                task = asyncio.create_task(self._command(message))
                self._command_tasks.add(task)
                task.add_done_callback(self._command_tasks.discard)
            elif message.kind == WireMessageKind.command_outcome_ack:
                self.registry.confirm_command(str(message.payload["command_id"]))
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

    async def _retire_source(self, message: WireMessage) -> None:
        request = TypeAdapter(SourceRetirementRequest).validate_python(message.payload)
        receipt = await self.registry.retire_source(message.request_id, request, self.identity)
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            await self._send(
                WireMessage(WireMessageKind.source_retired, message.request_id, as_object(receipt))
            )

    async def _command(self, message: WireMessage) -> None:
        command = TypeAdapter(ChildCommand).validate_python(message.payload)
        outcome = await self.registry.execute_command(command)
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            await self._send(
                WireMessage(WireMessageKind.command_outcome, message.request_id, as_object(outcome))
            )

    async def _preview(self, message: WireMessage) -> None:
        try:
            if (
                not self._handshake_accepted
                or self.state != ConnectionState.ready
                or self._preview_streams is None
            ):
                raise ConnectionError("preview generation is unavailable")
            lease_id = str(message.payload["lease_id"])
            if message.kind is WireMessageKind.preview_bind:
                await self._preview_streams.bind(lease_id, cast(int, message.payload["port"]))
            elif message.kind is WireMessageKind.preview_open:
                self._preview_streams.open(
                    lease_id, str(message.payload["stream_id"]), cast(int, message.payload["port"])
                )
            else:
                await self._preview_streams.release(lease_id)
            await self._send(WireMessage(WireMessageKind.preview_bound, message.request_id, {}))
        except (ValueError, OSError, ConnectionError, TimeoutError) as error:
            await self._send(
                WireMessage(WireMessageKind.error, message.request_id, {"message": str(error)})
            )

    async def _input(
        self, message: WireMessage, state: InputTransferDeadline | None = None
    ) -> None:
        # State is installed by the reader before scheduling, so an immediate
        # cancellation cannot miss an input whose worker has not started.
        if state is None:
            state = InputTransferDeadline(
                TypeAdapter(InputTransferRequest).validate_python(message.payload)
            )
            self._input_transfers[message.request_id] = state
        client, writer, identity = self._app_client, self._writer, self.identity
        request, slot = state.request, state.request.slot
        temporary = self.paths.run / ("input-" + uuid4().hex)
        self._transfers += 1

        async def send(kind: WireMessageKind, payload: JsonObject) -> None:
            async with self._write_lock:
                if writer is None or self._writer is not writer or self.identity != identity:
                    raise ConnectionError("input generation changed")
                if kind is WireMessageKind.file_accepted and not state.active():
                    return
                try:
                    writer.write(encode_frame(WireMessage(kind, message.request_id, payload)))
                    await writer.drain()
                except OSError as error:
                    writer.close()
                    raise ConnectionError("input control connection failed") from error

        async def progress(phase: str, count: int) -> None:
            if not state.active():
                raise asyncio.CancelledError()
            update = InputTransferProgress(
                request.command_id, slot.transfer_id, cast(Any, phase), count
            )
            state.advance(update)
            await send(WireMessageKind.input_progress, as_object(update))

        async def receive() -> None:
            if not state.active():
                return
            if client is None or not self._handshake_accepted or slot.direction != "download":
                raise ValueError("input generation or slot unavailable")
            self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Capacity covers download, verification, installation and deferred I/O.
            async with self._transfer_capacity:
                await client.download(slot, temporary, progress)
                if not state.active() or identity != self.identity or not self._handshake_accepted:
                    return
                await settled_io(self.inputs.receive_path, slot.manifest, temporary)
                if not state.active() or identity != self.identity or not self._handshake_accepted:
                    return
                await send(WireMessageKind.file_accepted, as_object(slot.manifest))

        worker = asyncio.create_task(receive())
        self._input_workers[message.request_id] = worker
        timer = asyncio.create_task(state.expired())
        try:
            done, _ = await asyncio.wait((worker, timer), return_when=asyncio.FIRST_COMPLETED)
            if worker not in done:
                state.revoked = True
                worker.cancel()
                await send(
                    WireMessageKind.input_failed,
                    as_object(
                        InputTransferFailed(
                            request.command_id, slot.transfer_id, "input transfer deadline expired"
                        )
                    ),
                )
            else:
                await worker
        except (OSError, ValueError, ConnectionError, aiohttp.ClientError) as error:
            state.revoked = True
            with contextlib.suppress(ConnectionError, OSError):
                await send(
                    WireMessageKind.input_failed,
                    as_object(
                        InputTransferFailed(request.command_id, slot.transfer_id, str(error))
                    ),
                )
        finally:
            state.revoked = True
            timer.cancel()
            worker.cancel()
            # Never abandon an admitted thread or release its capacity/source early.
            await wait_for_settlement(worker)
            await wait_for_settlement(timer)
            temporary.unlink(missing_ok=True)
            self._input_workers.pop(message.request_id, None)
            self._input_transfers.pop(message.request_id, None)
            self._transfers -= 1

    async def _replay(self) -> None:
        # Drain the pending prefix captured for this pass, not newly produced work.
        # Admit one head per conversation and refill capacity after every receipt.
        # A blocked conversation retains its bytes without stopping other heads.
        async with self._replay_lock:
            ceilings = await asyncio.to_thread(self.spool.pending_sequence_ceilings)
            tasks: dict[asyncio.Task[bool], str] = {}
            blocked: set[str] = set()

            async def submit(envelope: QueuedEnvelope) -> bool:
                try:
                    await self._replay_envelope(envelope)
                    return True
                except (TimeoutError, ConnectionError, BrokenPipeError, aiohttp.ClientError):
                    raise
                except (ValueError, OSError):
                    logging.getLogger(__name__).exception(
                        "executor conversation replay blocked: %s", envelope.conversation_id
                    )
                    return False

            async def admit() -> None:
                active = set(tasks.values())
                for envelope in await asyncio.to_thread(self.spool.pending_heads):
                    if len(tasks) >= MAX_TRANSFERS:
                        break
                    if (
                        envelope.sequence <= ceilings.get(envelope.conversation_id, 0)
                        and envelope.conversation_id not in active | blocked
                    ):
                        tasks[asyncio.create_task(submit(envelope))] = envelope.conversation_id
                        active.add(envelope.conversation_id)

            try:
                await admit()
                while tasks:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        conversation = tasks.pop(task)
                        if not await task:
                            blocked.add(conversation)
                    await admit()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _replay_envelope(self, envelope: QueuedEnvelope) -> None:
        identity = self.identity
        with self.spool.pin_snapshots(envelope.submission_id) as snapshots:
            for snapshot in snapshots:
                payload = envelope.payload
                child = payload.get("child")
                if child is None:
                    ask = cast(JsonObject, payload.get("ask", {}))
                    child = ask.get("child")
                if child is None:
                    raise ValueError("file publication lacks assigned child")
                publication: JsonObject = {"manifest": as_object(snapshot.manifest), "child": child}
                response = await self._request(WireMessageKind.file_manifest, publication)
                if identity != self.identity or not self._handshake_accepted:
                    raise ConnectionError("publication generation changed")
                if response.kind != WireMessageKind.file_accepted:
                    slot = TypeAdapter(FileTransfer).validate_python(response.payload)
                    if (
                        response.kind is not WireMessageKind.file_slot
                        or slot.direction != "upload"
                        or slot.manifest != snapshot.manifest
                        or self._app_client is None
                    ):
                        raise ValueError("invalid publication slot")
                    client = self._app_client
                    self._transfers += 1
                    try:
                        async with self._transfer_capacity:
                            assert client is not None
                            await client.upload(slot, snapshot.local_path)
                        if identity != self.identity or not self._handshake_accepted:
                            raise ConnectionError("publication generation changed")
                        completion = await self._request(WireMessageKind.file_complete, publication)
                        if (
                            completion.kind is not WireMessageKind.file_accepted
                            or completion.payload != as_object(snapshot.manifest)
                        ):
                            raise ValueError("publication did not confirm complete bytes")
                    finally:
                        self._transfers -= 1
                elif response.payload != as_object(snapshot.manifest):
                    raise ValueError("publication receipt manifest changed")
            if identity != self.identity or not self._handshake_accepted:
                raise ConnectionError("publication generation changed")
            response = await self._request(WireMessageKind.envelope, as_object(envelope))
            receipt = TypeAdapter(AcceptanceReceipt).validate_python(response.payload)
            await asyncio.to_thread(self.spool.acknowledge, receipt)
            future = self._receipts.get(envelope.submission_id)
            if future is not None and not future.done():
                future.set_result(receipt)

    async def _reconcile(self) -> None:
        inventory = self.registry.inventory(self.identity, include_pending=False)
        # Stream children and retained outcomes, never the potentially unbounded spool.
        # VPS aggregates these within the fenced generation before ready admission.
        batches = []
        for child in inventory.children:
            pieces = [
                replace(child, pending_asks=(ask,), pending_user_input_ids=())
                for ask in child.pending_asks
            ]
            pieces += [
                replace(child, pending_asks=(), pending_user_input_ids=(request_id,))
                for request_id in child.pending_user_input_ids
            ]
            if not pieces:
                pieces = [child]
            batches += [
                ReconciliationInventory(self.identity, (piece,), (), ()) for piece in pieces
            ]
        batches += [
            ReconciliationInventory(self.identity, (), (), (outcome,))
            for outcome in inventory.retained_command_outcomes
        ]
        batches.append(ReconciliationInventory(self.identity, (), (), ()))
        for batch in batches:
            response = await self._request(WireMessageKind.reconcile_inventory, as_object(batch))
            result = TypeAdapter(ReconciliationResult).validate_python(response.payload)
            if result.identity != self.identity:
                raise ValueError("stale reconciliation generation")
            for receipt in result.receipts:
                await asyncio.to_thread(self.spool.acknowledge, receipt)
            for command_id in result.acknowledged_command_ids:
                self.registry.confirm_command(command_id)
            for answer in result.answers:
                await self.registry.apply_answer(answer)

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
            async with client.websocket("/api/executor/connect", MAX_ENVELOPE_BYTES + 4) as peer:
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
                    from planner.previews.configuration import PREVIEW_CHUNK_BYTES

                    phase = ConnectionPhase.preview_setup
                    self._preview_streams = MacPreviewForwards(
                        lambda stream_id: client.websocket(
                            "/api/executor/previews/" + stream_id, PREVIEW_CHUNK_BYTES
                        ),
                        lambda: (
                            self.identity == identity
                            and self._handshake_accepted
                            and self.state == ConnectionState.ready
                            and not self.registry.admission_stopped
                        ),
                    )
                    phase = ConnectionPhase.reconcile1
                    await self._reconcile()
                    phase = ConnectionPhase.replay1
                    await self._replay()
                    phase = ConnectionPhase.reconcile2
                    await self._reconcile()
                    phase = ConnectionPhase.replay2
                    await self._replay()
                    phase = ConnectionPhase.ready_ack
                    await self._request(WireMessageKind.reconciliation_ready, as_object(identity))
                    self.lost_at_ms = None
                    self.state = ConnectionState.ready
                    phase = ConnectionPhase.steady_state
                    while not self._stop.is_set():
                        await self._replay()
                        await self._reconcile()
                        await self._replay()
                        await asyncio.sleep(RECONNECT_INTERVAL_SECONDS)

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
            previews, self._preview_streams = self._preview_streams, None
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
            tasks = [
                task
                for task in (reader_task, session_task, stop_task, *self._transfer_tasks)
                if task is not None
            ]
            for cleanup_task in tasks:
                cleanup_task.cancel()
            try:
                try:
                    if previews is not None:
                        await previews.close()
                finally:
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
                    if self._preview_streams is not None:
                        await self._preview_streams.close()
                    quiescence = await self.registry.quiesce()
                    pending_transfers = max(self._transfers, len(self._transfer_tasks))
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
                    asyncio.to_thread(self.spool.lookup_publication, retry.submission_id)
                )
                self._ipc_lookups.add(lookup)

                def looked_up(task: asyncio.Task[JournalPublication | None]) -> None:
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
                receipt: asyncio.Future[AcceptanceReceipt] = (
                    asyncio.get_running_loop().create_future()
                )
                self._receipts[request.request_id] = receipt

                async def publish_write() -> JournalPublication:
                    assert publication is not None
                    publication.admitted = True
                    previous = await asyncio.to_thread(
                        self.spool.lookup_publication, request.request_id
                    )
                    if previous is not None:
                        if previous.envelope.kind != EnvelopeKind.cli_write:
                            raise _IpcConflict("submission identity conflicts with journal")
                        self._match_publication(
                            TypeAdapter(CliWrite).validate_python(previous.envelope.payload), write
                        )
                        return previous
                    snapshots: tuple[FileSnapshot, ...] = ()
                    if child.ticket_id is not None:
                        snapshots = await asyncio.to_thread(
                            snapshot_ticket, self.paths, child.ticket_id
                        )
                    envelope = await asyncio.to_thread(
                        self.spool.mint_envelope,
                        conversation_id=child.reference.conversation_id,
                        kind=EnvelopeKind.cli_write,
                        payload=as_object(write),
                        snapshots=snapshots,
                        submission_id=request.request_id,
                    )
                    return JournalPublication(envelope, True)

                task = asyncio.create_task(
                    self.registry.source_work(child.reference, publish_write, new_work=True)
                )
                publication = _IpcPublication(write, task, receipt)
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
        self, request_id: str, submission_id: str, row: JournalPublication | None
    ) -> IpcResponse:
        if row is None:
            return self._unconfirmed(request_id, submission_id, "unknown")
        if row.pending and request_id != submission_id:
            return self._unconfirmed(request_id, submission_id, "queued")
        if row.pending:
            # Keep the existing durable queue contract for callers that already use it.
            return IpcResponse(request_id, None, None, QueuedCliResult(submission_id))
        return self._unconfirmed(request_id, submission_id, "acknowledged")

    def _release_publication(self, submission_id: str, publication: _IpcPublication) -> None:
        if not publication.task.done():
            return
        if not publication.task.cancelled():
            publication.task.exception()  # Consume failure even after every caller disconnects.
        if publication.observers == 0 and self._publications.get(submission_id) is publication:
            del self._publications[submission_id]
            if self._receipts.get(submission_id) is publication.receipt:
                del self._receipts[submission_id]
            if not publication.receipt.done():
                publication.receipt.cancel()

    async def _observe_publication(
        self, request_id: str, submission_id: str, publication: _IpcPublication, deadline: float
    ) -> IpcResponse:
        publication.observers += 1
        try:
            # Neither a timeout nor cancellation of this observer owns the source task.
            await asyncio.wait(
                (publication.task, publication.receipt),
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if publication.receipt.done() and not publication.receipt.cancelled():
                return self._receipt_response(request_id, publication.receipt.result())
            if publication.task.cancelled():
                return self._unconfirmed(request_id, submission_id, "unknown")
            if publication.task.done():
                error = publication.task.exception()
                if error is not None:
                    if not publication.admitted or isinstance(error, _IpcConflict):
                        raise error
                    # Even a local error can follow the journal commit. Do not infer refusal.
                    return self._unconfirmed(request_id, submission_id, "unknown")
                row = publication.task.result()
                if not publication.receipt.done() and row.pending:
                    await asyncio.wait(
                        (publication.receipt,), timeout=max(0, deadline - time.monotonic())
                    )
                if not publication.receipt.done():
                    return self._publication_status(request_id, submission_id, row)
            if publication.receipt.done() and not publication.receipt.cancelled():
                return self._receipt_response(request_id, publication.receipt.result())
            return self._unconfirmed(request_id, submission_id, "preparing")
        finally:
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
