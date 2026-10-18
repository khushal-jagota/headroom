"""Mac-dialed executor service: native children, durable replay and owner-only IPC."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import cast
from uuid import uuid4

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
    SPOOL_FORMAT_VERSION,
    ExecutorPaths,
)
from planner.executor.contracts import (
    AcceptanceReceipt,
    ChildCommand,
    ChildReference,
    CliWrite,
    ConnectionState,
    EnvelopeKind,
    ExecutorHealth,
    ExecutorIdentity,
    FileChunk,
    FileChunkAcknowledgment,
    FileManifest,
    FileSnapshot,
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
    SourceRetirementRequest,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import IncomingFiles, file_chunks, safe_component, snapshot_ticket
from planner.executor.journal import FilesystemSpool
from planner.executor.runtime import NativeChildRegistry, now_ms
from planner.executor.wire import (
    ProtocolRefused,
    encode_frame,
    parse_identity,
    read_frame,
    read_typed_frame,
    validate_file_chunk_ack,
)
from planner.previews.contracts import PREVIEW_KINDS
from planner.previews.streams import PreviewStreams


def as_object(value: object) -> JsonObject:
    return cast(JsonObject, encode_value(value))


class MacExecutor:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        revision: str,
        ssh_argv: Sequence[str],
        panels_server_url: str,
        registry: NativeChildRegistry | None = None,
    ) -> None:
        self.paths = paths
        self.ssh_argv = tuple(ssh_argv)
        self.spool = FilesystemSpool(paths.spool)
        self.identity = ExecutorIdentity(
            PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()), 0
        )
        self._handshake_accepted = False
        self._preview_streams: PreviewStreams | None = None
        self.state = ConnectionState.disconnected
        self.lost_at_ms: int | None = None
        self._writer: asyncio.StreamWriter | asyncio.subprocess.Process | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[WireMessage]] = {}
        self._receipts: dict[str, asyncio.Future[AcceptanceReceipt]] = {}
        self._stop = asyncio.Event()
        self.exit_committed = False
        self.inputs = IncomingFiles(
            paths.root / "artifacts" / "files", paths.run / "input-quarantine"
        )
        self._command_tasks: set[asyncio.Task[None]] = set()
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
            process = self._writer
            if not isinstance(process, asyncio.subprocess.Process) or process.stdin is None:
                raise ConnectionError("executor link unavailable")
            process.stdin.write(encode_frame(message))
            await process.stdin.drain()

    async def _request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        request_id = uuid4().hex
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(WireMessage(kind, request_id, payload))
            response = await future
            if response.kind in (WireMessageKind.error, WireMessageKind.protocol_refused):
                raise ValueError(str(response.payload))
            return response
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            message = await read_frame(reader)
            if message.kind in PREVIEW_KINDS:
                if not self._handshake_accepted or self._preview_streams is None:
                    raise ProtocolRefused("preview frame before validated handshake")
                self._preview_streams.feed(message)
                continue
            if not self._handshake_accepted and message.kind in (
                WireMessageKind.file_manifest,
                WireMessageKind.file_chunk,
                WireMessageKind.file_chunk_ack,
                WireMessageKind.file_complete,
                WireMessageKind.command,
                WireMessageKind.command_outcome_ack,
                WireMessageKind.source_retire,
            ):
                raise ProtocolRefused("peer sent work before validated handshake")
            if message.kind == WireMessageKind.file_manifest:
                self.inputs.begin(TypeAdapter(FileManifest).validate_python(message.payload))
                await self._send(WireMessage(WireMessageKind.file_manifest, message.request_id, {}))
            elif message.kind == WireMessageKind.file_chunk:
                chunk = TypeAdapter(FileChunk).validate_python(message.payload)
                self.inputs.chunk(chunk)
                acknowledgment = FileChunkAcknowledgment(
                    chunk.publication_id,
                    chunk.offset,
                    len(base64.b64decode(chunk.content_base64, validate=True)),
                )
                await self._send(
                    WireMessage(
                        WireMessageKind.file_chunk_ack,
                        message.request_id,
                        as_object(acknowledgment),
                    )
                )
            elif message.kind == WireMessageKind.file_complete:
                manifest = TypeAdapter(FileManifest).validate_python(message.payload)
                self.inputs.complete(manifest)
                await self._send(
                    WireMessage(
                        WireMessageKind.file_accepted, message.request_id, as_object(manifest)
                    )
                )
            elif message.kind == WireMessageKind.health and "clock_probe" in message.payload:
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
                        # Admit opens after the ready acknowledgment in wire order.
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

    async def _replay(self) -> None:
        blocked: set[str] = set()
        while True:
            heads = await asyncio.to_thread(self.spool.pending_heads)
            ready = tuple(row for row in heads if row.conversation_id not in blocked)
            if not ready:
                return
            for envelope in ready:
                try:
                    await self._replay_envelope(envelope)
                except (ConnectionError, BrokenPipeError):
                    raise
                except (ValueError, OSError):
                    # Keep that conversation's order while independent work drains.
                    blocked.add(envelope.conversation_id)
                    logging.getLogger(__name__).exception(
                        "executor conversation replay blocked: %s", envelope.conversation_id
                    )

    async def _replay_envelope(self, envelope: QueuedEnvelope) -> None:
        # A pin protects immutable bytes through receipt cleanup and cancellation.
        # It holds no database transaction while the transport awaits the peer.
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
                    for chunk in file_chunks(snapshot):
                        response = await self._request(WireMessageKind.file_chunk, as_object(chunk))
                        validate_file_chunk_ack(response, chunk)
                    await self._request(WireMessageKind.file_complete, publication)
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
        self.identity = replace(
            self.identity, connection_generation=self.identity.connection_generation + 1
        )
        self.state = ConnectionState.reconciling
        self._handshake_accepted = False
        try:
            process = await asyncio.create_subprocess_exec(
                *self.ssh_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            self.state = ConnectionState.disconnected
            if self.lost_at_ms is None:
                self.lost_at_ms = now_ms()
            raise
        self._writer = process
        identity = self.identity

        async def send_preview(message: WireMessage) -> None:
            async with self._write_lock:
                if (
                    self._writer is not process
                    or self.identity != identity
                    or self.state is not ConnectionState.ready
                    or process.stdin is None
                ):
                    raise ConnectionError("preview executor generation changed")
                process.stdin.write(encode_frame(message))
                await process.stdin.drain()

        self._preview_streams = PreviewStreams(
            identity,
            send_preview,
            lambda: (
                self._handshake_accepted
                and self.state is ConnectionState.ready
                and self._writer is process
                and self.identity == identity
            ),
            accept_open=True,
        )
        assert process.stdout is not None
        reader_task = asyncio.create_task(self._read_loop(process.stdout))

        async def session() -> None:
            response = await self._request(WireMessageKind.hello, as_object(self.health()))
            peer = parse_identity(response.payload)
            if peer != self.identity:
                raise ValueError("handshake identity changed")
            self._handshake_accepted = True
            await self._reconcile()
            await self._replay()
            await self._reconcile()
            await self._replay()
            await self._request(WireMessageKind.reconciliation_ready, as_object(self.identity))
            # This is the current outage marker, not a historical loss timestamp.
            # Clear it only after this generation finishes reconciliation.
            self.lost_at_ms = None
            self.state = ConnectionState.ready
            while not self._stop.is_set():
                await self._replay()
                await self._reconcile()
                await self._replay()
                await asyncio.sleep(RECONNECT_INTERVAL_SECONDS)

        session_task = asyncio.create_task(session())
        stop_task = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait(
                (reader_task, session_task, stop_task), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                await task
        finally:
            self._handshake_accepted = False
            if self._preview_streams is not None:
                await self._preview_streams.close()
                self._preview_streams = None
            self.state = ConnectionState.disconnected
            if self.lost_at_ms is None:
                self.lost_at_ms = now_ms()
            self._writer = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("executor link lost"))
            for task in (reader_task, session_task, stop_task):
                task.cancel()
            await asyncio.gather(reader_task, session_task, stop_task, return_exceptions=True)
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
            # Process.wait also waits for pipe transport closure. A canceled frame
            # reader can leave stdout paused above its high-water mark even after
            # the SSH/relay process exits. Drain the pipe before awaiting that exit.
            await process.communicate()

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
                    quiescence = await self.registry.quiesce()
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
                    snapshots = snapshot_ticket(self.paths, child.ticket_id)
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
                    except (OSError, ValueError, ConnectionError, asyncio.IncompleteReadError):
                        if not self._stop.is_set():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(
                                    self._stop.wait(), RECONNECT_INTERVAL_SECONDS
                                )
        finally:
            self.paths.ipc_socket.unlink(missing_ok=True)
            self.spool.close()
