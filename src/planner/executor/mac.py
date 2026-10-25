"""Mac-dialed executor service: native children, durable replay and owner-only IPC."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import asyncssh
from pydantic import TypeAdapter

from planner.conversation.backends.claude_agent_sdk import ClaudeAgentSdkBackendChildFactory
from planner.conversation.backends.codex_app_server import CodexAppServerBackendChildFactory
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.backends.hermes_acp import HermesAcpBackendChildFactory
from planner.conversation.contracts import ConversationBackendKey, ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.production_backends import production_backend_launches
from planner.executor.codec import encode_value
from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
    PROTOCOL_VERSION,
    RECONNECT_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    SFTP_MAX_TRANSFERS,
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
    ExecutorSshConfig,
    FileSnapshot,
    FileTransferSlot,
    FrameReader,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    JsonObject,
    LiveReadRequest,
    QueuedCliResult,
    QueuedEnvelope,
    QuiescenceReceipt,
    QuiesceRequest,
    ReceiptDisposition,
    ReconciliationInventory,
    ReconciliationResult,
    SftpEndpoint,
    SourceRetirementRequest,
    WireMessage,
    WireMessageKind,
)
from planner.executor.diagnostics import connection_diagnostics
from planner.executor.files import IncomingFiles, safe_component, snapshot_ticket
from planner.executor.journal import FilesystemSpool
from planner.executor.runtime import NativeChildRegistry, now_ms
from planner.executor.ssh import (
    close_connection,
    connect_outer,
    connect_sftp,
    download_slot,
    upload_snapshot,
)
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


class MacExecutor:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        revision: str,
        ssh_config: ExecutorSshConfig,
        remote_command: str = "panels executor relay",
        panels_server_url: str,
        registry: NativeChildRegistry | None = None,
    ) -> None:
        self.paths = paths
        self.ssh_config = ssh_config
        self.remote_command = remote_command
        self._sftp: asyncssh.SFTPClient | None = None
        self._transfers = 0
        self._transfer_capacity = asyncio.Semaphore(SFTP_MAX_TRANSFERS)
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
        self._writer: asyncssh.SSHWriter[bytes] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[WireMessage]] = {}
        self._receipts: dict[str, asyncio.Future[AcceptanceReceipt]] = {}
        self._stop = asyncio.Event()
        self.exit_committed = False
        self.inputs = IncomingFiles(
            paths.root / "artifacts" / "files", paths.run / "input-quarantine"
        )
        self._command_tasks: set[asyncio.Task[None]] = set()
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
            if message.kind in (WireMessageKind.preview_bind, WireMessageKind.preview_release):
                task = asyncio.create_task(self._preview(message))
                self._command_tasks.add(task)
                task.add_done_callback(self._command_tasks.discard)
                continue
            if (
                message.kind == WireMessageKind.file_slot
                and message.request_id not in self._pending
            ):
                if len(self._transfer_tasks) >= SFTP_MAX_TRANSFERS:
                    raise ProtocolRefused("input transfer capacity exceeded")
                task = asyncio.create_task(self._input(message))
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
            path = str(message.payload["path"])
            if message.kind is WireMessageKind.preview_bind:
                await self._preview_streams.bind(path, cast(int, message.payload["port"]))
            else:
                await self._preview_streams.release(path)
            await self._send(WireMessage(WireMessageKind.preview_bound, message.request_id, {}))
        except (ValueError, OSError, ConnectionError, TimeoutError) as error:
            await self._send(
                WireMessage(WireMessageKind.error, message.request_id, {"message": str(error)})
            )

    async def _input(self, message: WireMessage) -> None:
        if not self._handshake_accepted or self._sftp is None or self._transport_admission_stopped:
            await self._send(
                WireMessage(
                    WireMessageKind.error,
                    message.request_id,
                    {"message": "input generation does not admit transfers"},
                )
            )
            return
        try:
            slot = TypeAdapter(FileTransferSlot).validate_python(message.payload)
            if slot.direction != "download" or self._transfers >= SFTP_MAX_TRANSFERS:
                raise ValueError("invalid input slot or transfer capacity")
        except ValueError as error:
            await self._send(
                WireMessage(WireMessageKind.error, message.request_id, {"message": str(error)})
            )
            return
        identity = self.identity
        self._transfers += 1
        self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.paths.run / ("input-" + uuid4().hex)
        try:
            async with self._transfer_capacity:
                await download_slot(self._sftp, slot.token, temporary)
            if identity != self.identity or not self._handshake_accepted:
                raise ConnectionError("input generation changed")
            # Cancellation cannot stop disk work. Drain it before deleting its source.
            installation = asyncio.create_task(
                asyncio.to_thread(self.inputs.receive_path, slot.manifest, temporary)
            )
            try:
                await asyncio.shield(installation)
            except BaseException:
                await asyncio.gather(installation, return_exceptions=True)
                raise
            if identity != self.identity or not self._handshake_accepted:
                raise ConnectionError("input generation changed")
            await self._send(
                WireMessage(
                    WireMessageKind.file_accepted, message.request_id, as_object(slot.manifest)
                )
            )
        except (OSError, ValueError, ConnectionError, asyncssh.Error) as error:
            with contextlib.suppress(ConnectionError):
                await self._send(
                    WireMessage(WireMessageKind.error, message.request_id, {"message": str(error)})
                )
        finally:
            self._transfers -= 1
            temporary.unlink(missing_ok=True)

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
                except (TimeoutError, ConnectionError, BrokenPipeError, asyncssh.Error):
                    raise
                except (ValueError, OSError):
                    logging.getLogger(__name__).exception(
                        "executor conversation replay blocked: %s", envelope.conversation_id
                    )
                    return False

            async def admit() -> None:
                active = set(tasks.values())
                for envelope in await asyncio.to_thread(self.spool.pending_heads):
                    if len(tasks) >= SFTP_MAX_TRANSFERS:
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
                if response.kind != WireMessageKind.file_accepted:
                    slot = TypeAdapter(FileTransferSlot).validate_python(response.payload)
                    if (
                        slot.direction != "upload"
                        or slot.manifest != snapshot.manifest
                        or self._sftp is None
                    ):
                        raise ValueError("invalid publication slot")
                    self._transfers += 1
                    try:
                        async with self._transfer_capacity:
                            await upload_snapshot(self._sftp, snapshot.local_path, slot.token)
                        completion = await self._request(WireMessageKind.file_complete, publication)
                        if completion.kind is not WireMessageKind.file_accepted:
                            raise ValueError("publication did not confirm complete bytes")
                    finally:
                        self._transfers -= 1
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
        phase = ConnectionPhase.outer_connect

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
        try:
            outer = await observe(connect_outer(self.ssh_config), ConnectionFailureOrigin.session)
        except BaseException:
            self.state = ConnectionState.disconnected
            if self.lost_at_ms is None:
                self.lost_at_ms = now_ms()
            raise
        try:
            phase = ConnectionPhase.relay_start
            process = await observe(
                outer.create_process(self.remote_command, encoding=None),
                ConnectionFailureOrigin.session,
            )
        except BaseException:
            self.state = ConnectionState.disconnected
            if self.lost_at_ms is None:
                self.lost_at_ms = now_ms()
            await close_connection(outer)
            raise
        self._writer = process.stdin
        identity = self.identity
        phase = ConnectionPhase.hello
        reader_task = asyncio.create_task(
            observe(self._read_loop(process.stdout), ConnectionFailureOrigin.reader)
        )
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        sftp_context = None

        async def session() -> None:
            nonlocal phase
            response = await self._request(WireMessageKind.hello, as_object(self.health()))
            peer = parse_identity(response.payload)
            if peer != self.identity:
                raise ValueError("handshake identity changed")
            self._handshake_accepted = True
            phase = ConnectionPhase.transport_open
            opened = await self._request(
                WireMessageKind.transport_open,
                {
                    "client_public_key": client_key.export_public_key().decode(),
                    "identity": as_object(identity),
                },
            )
            if opened.kind is not WireMessageKind.transport_opened:
                raise ValueError("restricted SFTP endpoint refused")
            endpoint = TypeAdapter(SftpEndpoint).validate_python(opened.payload["endpoint"])
            nonlocal sftp_context
            phase = ConnectionPhase.sftp_open
            opening_sftp = connect_sftp(outer, endpoint, client_key, self.paths.run)
            self._sftp = await opening_sftp.__aenter__()
            sftp_context = opening_sftp
            phase = ConnectionPhase.preview_setup
            self._preview_streams = MacPreviewForwards(
                outer,
                Path(str(opened.payload["preview_root"])),
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
            await self._request(WireMessageKind.reconciliation_ready, as_object(self.identity))
            # This is the current outage marker, not a historical loss timestamp.
            # Clear it only after this generation finishes reconciliation.
            self.lost_at_ms = None
            self.state = ConnectionState.ready
            phase = ConnectionPhase.steady_state
            while not self._stop.is_set():
                await self._replay()
                await self._reconcile()
                await self._replay()
                await asyncio.sleep(RECONNECT_INTERVAL_SECONDS)

        session_task = asyncio.create_task(observe(session(), ConnectionFailureOrigin.session))
        stop_task = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait(
                (reader_task, session_task, stop_task), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                await task
        finally:
            # Revoke generation access before any fallible cleanup can yield.
            self._handshake_accepted = False
            previews, self._preview_streams = self._preview_streams, None
            self.state = ConnectionState.disconnected
            if self.lost_at_ms is None:
                self.lost_at_ms = now_ms()
            self._writer = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("executor link lost"))
            transfers = tuple(self._transfer_tasks)
            for task in (reader_task, session_task, stop_task, *transfers):
                task.cancel()
            # A canceled SFTP operation can await FXP_CLOSE in its context exit.
            # Terminate channels before waiting for those canceled operations.
            try:
                await close_connection(outer)
            finally:
                try:
                    await asyncio.gather(
                        reader_task,
                        session_task,
                        stop_task,
                        *transfers,
                        return_exceptions=True,
                    )
                finally:
                    self._sftp = None
                    try:
                        if previews is not None:
                            await previews.close()
                    finally:
                        if sftp_context is not None:
                            await sftp_context.__aexit__(None, None, None)

    async def handle_ipc(self, request: IpcRequest) -> IpcResponse:
        try:
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
            self.registry.begin_source_work(child.reference)
            try:
                payload = as_object(
                    CliWrite(
                        str(request.payload["operation"]),
                        cast(JsonObject, request.payload["arguments"]),
                        child.reference,
                        child.active_turn,
                    )
                )
                snapshots: tuple[FileSnapshot, ...] = ()
                if child.ticket_id is not None:
                    snapshots = await asyncio.to_thread(
                        snapshot_ticket, self.paths, child.ticket_id
                    )
                envelope = await asyncio.to_thread(
                    self.spool.mint_envelope,
                    conversation_id=child.reference.conversation_id,
                    kind=EnvelopeKind.cli_write,
                    payload=payload,
                    snapshots=snapshots,
                    submission_id=request.request_id,
                )
            finally:
                self.registry.end_source_work(child.reference)
            future: asyncio.Future[AcceptanceReceipt] = asyncio.get_running_loop().create_future()
            self._receipts[envelope.submission_id] = future
            try:
                receipt = await asyncio.wait_for(asyncio.shield(future), CLI_RESULT_TIMEOUT_SECONDS)
                if receipt.disposition == ReceiptDisposition.refused:
                    error = receipt.result.get("error", receipt.result)
                    return IpcResponse(request.request_id, None, cast(JsonObject, error))
                return IpcResponse(request.request_id, receipt.result, None)
            except TimeoutError:
                return IpcResponse(
                    request.request_id, None, None, QueuedCliResult(envelope.submission_id)
                )
            finally:
                self._receipts.pop(envelope.submission_id, None)
        except (ValueError, PermissionError, ConnectionError) as error:
            return IpcResponse(
                request.request_id, None, {"message": str(error), "type": type(error).__name__}
            )

    async def _ipc_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_typed_frame(reader, IpcRequest)
            response = await self.handle_ipc(request)
            writer.write(encode_frame(response))
            await writer.drain()
        finally:
            writer.close()
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
                        asyncssh.Error,
                    ) as error:
                        connection_diagnostics.report(error)
                        if not self._stop.is_set():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(
                                    self._stop.wait(), RECONNECT_INTERVAL_SECONDS
                                )
        finally:
            self.paths.ipc_socket.unlink(missing_ok=True)
            self.spool.close()
