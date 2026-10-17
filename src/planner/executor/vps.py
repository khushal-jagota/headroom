"""The VPS server endpoint and the SSH stdio relay's local Unix connection."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import BackendEventSink, BackendSpawnFailed
from planner.conversation.contracts import ResolvedConversationStart
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.executor.codec import encode_value
from planner.executor.contracts import (
    AcceptanceReceipt,
    AnswerApplication,
    AskReference,
    AuthorizedAction,
    BackendEvent,
    BackendEventMethod,
    ChildCommand,
    ChildInventory,
    ChildReference,
    CliWrite,
    ClockSample,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ExecutorHealth,
    ExecutorIdentity,
    FileChunk,
    FileManifest,
    FilePublicationRequest,
    FileScope,
    JsonObject,
    LiveReadRequest,
    QueuedEnvelope,
    ReconciliationInventory,
    ReconciliationResult,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mutations import write_ticket_mutation
from planner.executor.remote import RemoteBackendChild, RemoteBackendChildFactory
from planner.executor.store import ExecutorStore
from planner.executor.wire import parse_identity, read_frame, write_frame


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def vps_socket_path(db_path: str) -> Path:
    return (
        Path(
            os.environ.get(
                "PLAN_EXECUTOR_VPS_SOCKET", str(Path(db_path).resolve().parent / "executor.sock")
            )
        )
        .expanduser()
        .resolve()
    )


class ExecutorEndpoint:
    """One active Mac connection; old generations are fenced before any writes."""

    def __init__(self, db_path: str, clock: Clock) -> None:
        self.store = ExecutorStore(db_path)
        self.clock = clock
        self.db_path = db_path
        self.runtime: Any = None
        self.app: Any = None
        self.identity: ExecutorIdentity | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.ready = False
        self._server: asyncio.Server | None = None
        self.children: dict[str, RemoteBackendChild] = {}
        self.sinks: dict[str, BackendEventSink] = {}
        self.pending_commands: dict[str, tuple[ChildCommand, asyncio.Future[CommandOutcome]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._inventory: dict[str, ChildInventory] = {}
        self._send_lock = asyncio.Lock()
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self.factory = RemoteBackendChildFactory(self.assign_child, self.send_command)
        self.file_receiver: Any = None
        self._responses: dict[str, asyncio.Future[WireMessage]] = {}
        self.clock_sample: ClockSample | None = None
        self._file_assignments: dict[str, ChildReference] = {}

    async def start(self) -> None:
        path = vps_socket_path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # A live socket belongs to another process. Never unlink it silently.
            try:
                reader, writer = await asyncio.open_unix_connection(str(path))
            except (ConnectionRefusedError, FileNotFoundError):
                path.unlink(missing_ok=True)
            else:
                del reader
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("executor socket already has a listener")
        self._server = await asyncio.start_unix_server(self._session, path=str(path))
        path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        vps_socket_path(self.db_path).unlink(missing_ok=True)

    def assign_child(
        self, start: ResolvedConversationStart, sink: BackendEventSink
    ) -> ChildReference:
        if self.identity is None or not self.ready:
            raise BackendSpawnFailed("The Mac executor has no reconciled connection")
        child = ChildReference(
            self.identity.executor_incarnation, str(uuid4()), start.conversation_id
        )
        env = (
            {}
            if start.role_materials is None
            else dict(start.role_materials.identity_environment_variables)
        )
        actor = env.get("PLAN_ACTOR")
        if actor == "worker":
            principal = Principal(PrincipalKind.ticket, env["PLAN_TICKET_ID"])
        elif actor == "chief":
            principal = Principal(PrincipalKind.chief, "chief")
        elif actor == "sprint_item_supervisor":
            principal = Principal(PrincipalKind.sprint_item, env["PLAN_SPRINT_ITEM_ID"])
        else:
            raise BackendSpawnFailed("remote children need a server-assigned Worker identity")
        self.store.register_child(child, principal.kind.value, principal.id, now_ms())
        self.sinks[child.child_id] = sink
        proxy = RemoteBackendChild(child, self.send_command)
        self.children[child.child_id] = proxy
        return child

    async def _send(self, message: WireMessage) -> None:
        async with self._send_lock:
            writer = self.writer
            if writer is None:
                raise ConnectionError("executor link unavailable")
            await write_frame(writer, message)

    async def _request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        request_id = str(uuid4())
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        try:
            await self._send(WireMessage(kind, request_id, payload))
            return await future
        finally:
            self._responses.pop(request_id, None)

    async def _transfer_command_inputs(self, command: ChildCommand) -> None:
        from planner.executor.configuration import FILE_CHUNK_BYTES

        seen: set[str] = set()
        for key in ("content", "sender_content"):
            content = command.arguments.get(key)
            if not isinstance(content, list):
                continue
            for piece in content:
                if not isinstance(piece, dict) or not isinstance(piece.get("stored_file_id"), str):
                    continue
                stored_file_id = str(piece["stored_file_id"])
                if stored_file_id in seen:
                    continue
                seen.add(stored_file_id)
                contents = await self.runtime.message_files.read(
                    command.child.conversation_id, stored_file_id
                )
                digest = hashlib.sha256(contents).hexdigest()
                manifest = FileManifest(
                    digest,
                    FileScope.conversation,
                    command.child.conversation_id,
                    stored_file_id,
                    digest,
                    len(contents),
                    str(piece.get("media_type", "application/octet-stream")),
                )
                await self._request(WireMessageKind.file_manifest, _object(manifest))
                for offset in range(0, len(contents), FILE_CHUNK_BYTES):
                    chunk = FileChunk(
                        manifest.publication_id,
                        offset,
                        base64.b64encode(contents[offset : offset + FILE_CHUNK_BYTES]).decode(
                            "ascii"
                        ),
                    )
                    await self._send(
                        WireMessage(WireMessageKind.file_chunk, str(uuid4()), _object(chunk))
                    )
                response = await self._request(WireMessageKind.file_complete, _object(manifest))
                if response.kind is not WireMessageKind.file_accepted:
                    raise ValueError("Mac did not confirm complete input bytes")

    async def send_command(self, command: ChildCommand) -> CommandOutcome:
        future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        self.pending_commands[command.command_id] = (command, future)
        try:
            if self.ready and self.writer is not None:
                try:
                    await self._transfer_command_inputs(command)
                    await self._send(
                        WireMessage(WireMessageKind.command, command.command_id, _object(command))
                    )
                except (OSError, ConnectionError):
                    pass  # The exact command remains pending across a link loss.
            return await asyncio.shield(future)
        finally:
            if future.done():
                self.pending_commands.pop(command.command_id, None)

    def _assignment(self, child: ChildReference) -> Principal:
        binding = self.store.child_assignment(child)
        if binding is None:
            raise PlannerError(ErrorCode.agent_forbidden, "child has no VPS assignment", {})
        return Principal(PrincipalKind(binding[0]), binding[1])

    async def accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        lock = self._conversation_locks.setdefault(envelope.conversation_id, asyncio.Lock())
        async with lock:
            return await self._accept_envelope(envelope, identity)

    async def _accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        if envelope.kind is EnvelopeKind.backend_event:
            event = TypeAdapter(BackendEvent).validate_python(envelope.payload)
            self._assignment(event.child)
            if event.child.conversation_id != envelope.conversation_id:
                raise ValueError("event conversation conflicts with envelope")

            def accept(row: QueuedEnvelope, write: Any) -> AcceptanceReceipt:
                def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    conn = connection
                    accepted = envelope
                    result = write(conn, accepted)
                    if (
                        event.method is BackendEventMethod.permission_ask_raised
                        and event.turn is not None
                    ):
                        from planner.executor.codec import decode_event_arguments

                        ask = decode_event_arguments(event)["ask"]
                        self.store.publish_ask_in_transaction(
                            conn,
                            AskReference(event.child, event.turn, ask.ask_id),
                            tuple(option.option_id for option in ask.options),
                            now_ms(),
                        )
                    if event.method is BackendEventMethod.turn_ended and event.turn is not None:
                        self.store.revoke_turn_callbacks_in_transaction(
                            conn, event.child, event.turn, identity, now_ms()
                        )
                    return cast(JsonObject, result)

                return self.store.accept(row, effect, now_ms(), identity=identity)

            return cast(
                AcceptanceReceipt,
                await self.runtime.system.receive_remote_backend_event(event, envelope, accept),
            )
        if envelope.kind is EnvelopeKind.cli_write:
            write = TypeAdapter(CliWrite).validate_python(envelope.payload)
            principal = self._assignment(write.child)
            if write.child.conversation_id != envelope.conversation_id:
                raise ValueError("CLI conversation conflicts with envelope")
            if write.operation in ("message.send", "ticket.request_help"):
                return await self._native_message(envelope, write, principal, identity)
            return self.store.accept(
                envelope,
                lambda conn, row: write_ticket_mutation(conn, write, principal, self.clock),
                now_ms(),
                identity=identity,
            )
        if envelope.kind is EnvelopeKind.command_outcome:
            outcome = TypeAdapter(CommandOutcome).validate_python(envelope.payload)
            self._assignment(outcome.child)
            application_payload = outcome.result.get("answer_application")
            if isinstance(application_payload, dict):
                application = TypeAdapter(AnswerApplication).validate_python(application_payload)
                if application.ask.child != outcome.child:
                    raise ValueError("answer application conflicts with command child")

                def record(connection: Any, applied: AnswerApplication) -> None:
                    self.store.record_application_in_transaction(connection, applied)
                    self.store.record_command_outcome_in_transaction(connection, outcome)

                receipt = await self.runtime.system.receive_remote_answer_application(
                    application,
                    envelope,
                    lambda row, write: self.store.accept(row, write, now_ms(), identity=identity),
                    record,
                )
                self._resolve_command(outcome)
                return cast(AcceptanceReceipt, receipt)
            receipt = self.store.accept(
                envelope,
                lambda conn, row: self._command_outcome(conn, outcome),
                now_ms(),
                identity=identity,
            )
            self._resolve_command(outcome)
            return receipt
        if envelope.kind is EnvelopeKind.answer_application:
            application = TypeAdapter(AnswerApplication).validate_python(envelope.payload)
            self._assignment(application.ask.child)
            return cast(
                AcceptanceReceipt,
                await self.runtime.system.receive_remote_answer_application(
                    application,
                    envelope,
                    lambda row, write: self.store.accept(row, write, now_ms(), identity=identity),
                    self.store.record_application_in_transaction,
                ),
            )
        if envelope.kind is EnvelopeKind.authorized_action:
            action = TypeAdapter(AuthorizedAction).validate_python(envelope.payload)
            self._assignment(action.ask.child)
            return self.store.accept(
                envelope, lambda conn, row: self._action(conn, action), now_ms(), identity=identity
            )
        raise ValueError("unknown executor envelope")

    def _command_outcome(self, conn: Any, outcome: CommandOutcome) -> JsonObject:
        self.store.record_command_outcome_in_transaction(conn, outcome)
        return {"recorded": True}

    def _application(self, conn: Any, application: AnswerApplication) -> JsonObject:
        self.store.record_application_in_transaction(conn, application)
        return {"recorded": True}

    def _action(self, conn: Any, action: AuthorizedAction) -> JsonObject:
        self.store.record_action_in_transaction(conn, action, self.clock_sample)
        return {"recorded": True}

    def _resolve_command(self, outcome: CommandOutcome) -> None:
        pending = self.pending_commands.get(outcome.command_id)
        if pending is not None and pending[0].child == outcome.child and not pending[1].done():
            pending[1].set_result(outcome)

    async def _native_message(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        principal: Principal,
        identity: ExecutorIdentity,
    ) -> AcceptanceReceipt:
        previous = self.store.preflight(envelope, identity=identity)
        if previous is not None:
            return self.store.accept(envelope, lambda conn, row: {}, now_ms(), identity=identity)
        # The sender ID is the domain's existing durable retry identity. It is
        # reused across every transport retry, never generated by an HTTP wrapper.
        from planner.core.authctx import RequestContext
        from planner.core.db import connect
        from planner.message_delivery import api, service
        from planner.message_delivery.contracts import MessageDeliveryMode

        conn = connect(self.db_path)
        try:
            body = write.arguments.get("body")
            if not isinstance(body, dict):
                raise PlannerError(ErrorCode.validation, "message body must be an object", {})
            recipient = (
                api._principal(body.get("target"))
                if write.operation == "message.send"
                else (
                    api._principal(body["recipient"])
                    if body.get("recipient")
                    else self._help_recipient(conn, write, principal)
                )
            )
            service.require_reach(conn, principal, recipient)
            result = await service.send_message(
                self.runtime.system,
                conn,
                self.clock,
                RequestContext(principal),
                recipient,
                api._message_text(body.get("message")),
                MessageDeliveryMode(str(body.get("mode", "steer"))),
                sender_message_id=envelope.submission_id,
            )
            outcome = api._result_json(result)
        except PlannerError as error:
            rejection = error

            def refuse(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                raise rejection

            return self.store.accept(envelope, refuse, now_ms(), identity=identity)
        finally:
            conn.close()
        return self.store.accept(
            envelope, lambda connection, row: outcome, now_ms(), identity=identity
        )

    def _help_recipient(self, conn: Any, write: CliWrite, principal: Principal) -> Principal:
        from planner.tickets import data

        ticket_id = write.arguments.get("ticket_id")
        if not isinstance(ticket_id, str) or principal != Principal(
            PrincipalKind.ticket, ticket_id
        ):
            raise PlannerError(
                ErrorCode.agent_forbidden, "only the Ticket Worker can request help", {}
            )
        return data.read_ticket(conn, ticket_id).ceiling_holder

    def owns_conversation(self, conversation_id: str) -> bool:
        return any(
            child.reference.conversation_id == conversation_id for child in self.children.values()
        )

    def ask_status(self, conversation_id: str, ask_id: str) -> JsonObject:
        for evidence in self.store.evidence_for_conversation(conversation_id):
            if evidence.ask.ask_id == ask_id:
                return {
                    "executor_evidence": _object(evidence),
                    "answer_available": self.ready
                    and self.identity is not None
                    and evidence.connection_generation == self.identity.connection_generation
                    and evidence.reachability.value == "reachable"
                    and evidence.answer_id is None,
                }
        return {"answer_available": False, "executor_evidence": None}

    async def answer_permission(self, conversation_id: str, ask_id: str, option_id: str) -> bool:
        from planner.executor.contracts import ChildOperation

        if not self.ready or self.identity is None:
            return False
        for evidence in self.store.evidence_for_conversation(conversation_id):
            if evidence.ask.ask_id != ask_id:
                continue
            try:
                answer = self.store.choose_answer(
                    evidence.ask, option_id, self.identity.connection_generation, now_ms()
                )
            except PlannerError:
                return False
            # The selection is durable before any native attempt. A timeout or
            # drop keeps it in the store for reconciliation, never permits replacement.
            command = ChildCommand(
                str(uuid4()),
                evidence.ask.child,
                ChildOperation.answer_permission_ask,
                evidence.ask.turn,
                {"answer": _object(answer)},
            )
            task = asyncio.create_task(self.send_command(command))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return True
        return False

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        identity = None
        try:
            hello = await read_frame(reader)
            if hello.kind is not WireMessageKind.hello:
                raise ValueError("executor handshake required")
            parsed_identity = parse_identity(hello.payload.get("identity"))
            health = TypeAdapter(ExecutorHealth).validate_python(hello.payload)
            if health.identity != parsed_identity:
                raise ValueError("identity coercion refused")
            identity = health.identity
            old_identity = self.identity
            old_writer = self.writer
            self.store.begin_connection(identity, now_ms())
            self.identity, self.writer, self.ready = identity, writer, False
            self._inventory = {}
            if old_writer is not None and old_writer is not writer:
                old_writer.close()
            if (
                old_identity is not None
                and old_identity.executor_incarnation != identity.executor_incarnation
            ):
                for command, future in self.pending_commands.values():
                    if not future.done():
                        future.set_result(
                            CommandOutcome(
                                command.command_id,
                                command.child,
                                CommandDisposition.uncertain,
                                {"reason": "executor_process_replaced"},
                            )
                        )
            await self._send(
                WireMessage(WireMessageKind.hello_accepted, hello.request_id, _object(identity))
            )
            probe = asyncio.create_task(self._measure_clock())
            self._tasks.add(probe)
            probe.add_done_callback(self._tasks.discard)
            while self.writer is writer and self.identity == identity:
                message = await read_frame(reader)
                task = asyncio.create_task(self._handle(message, identity, writer))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            pass
        finally:
            if identity is not None and self.writer is writer:
                self.ready = False
                self.writer = None
                self.store.mark_lost(identity, now_ms())
                for response_future in self._responses.values():
                    if not response_future.done():
                        response_future.set_exception(ConnectionError("executor link lost"))
            writer.close()

    async def _measure_clock(self) -> None:
        sent = now_ms()
        response = await self._request(
            WireMessageKind.health, {"clock_probe": {"vps_sent_at_ms": sent}}
        )
        self.clock_sample = ClockSample(
            sent,
            int(cast(int, response.payload["mac_received_at_ms"])),
            int(cast(int, response.payload["mac_sent_at_ms"])),
            now_ms(),
        )

    async def _handle(
        self, message: WireMessage, identity: ExecutorIdentity, writer: asyncio.StreamWriter
    ) -> None:
        if self.writer is not writer or self.identity != identity:
            return
        try:
            pending_response = self._responses.get(message.request_id)
            if pending_response is not None:
                if not pending_response.done():
                    pending_response.set_result(message)
                return
            response_kind = WireMessageKind.error
            result: JsonObject
            if message.kind is WireMessageKind.reconcile_inventory:
                inventory = TypeAdapter(ReconciliationInventory).validate_python(message.payload)
                if inventory.identity != identity:
                    raise ValueError("stale reconciliation generation")
                for item in inventory.children:
                    self._assignment(item.child)
                    previous = self._inventory.get(item.child.child_id)
                    if previous is not None and previous.active_turn == item.active_turn:
                        item = replace(
                            item,
                            pending_asks=tuple(
                                dict.fromkeys((*previous.pending_asks, *item.pending_asks))
                            ),
                            pending_user_input_ids=tuple(
                                dict.fromkeys(
                                    (*previous.pending_user_input_ids, *item.pending_user_input_ids)
                                )
                            ),
                        )
                    self._inventory[item.child.child_id] = item
                    proxy = self.children.get(item.child.child_id)
                    if proxy is None:
                        proxy = RemoteBackendChild(item.child, self.send_command)
                        self.children[item.child.child_id] = proxy
                    await self.runtime.system.adopt_remote_child(item, proxy)
                for outcome in inventory.retained_command_outcomes:
                    self._resolve_command(outcome)
                if not inventory.children and not inventory.retained_command_outcomes:
                    merged = replace(inventory, children=tuple(self._inventory.values()))
                    result = _object(self.store.reconcile(merged, now_ms()))
                    self._inventory = {}
                else:
                    result = _object(ReconciliationResult(identity, (), (), (), ()))
                response_kind = WireMessageKind.reconcile_result
            elif message.kind is WireMessageKind.reconciliation_ready:
                self.ready = True
                if hasattr(self.store, "mark_ready"):
                    self.store.mark_ready(identity, now_ms())
                result, response_kind = {"ready": True}, WireMessageKind.reconciliation_ready
                # Resending a stable command to the same live registry is safe:
                # the registry retains its pending task and first outcome.
                for command, future in self.pending_commands.values():
                    if (
                        not future.done()
                        and command.child.executor_incarnation == identity.executor_incarnation
                    ):
                        await self._send(
                            WireMessage(
                                WireMessageKind.command, command.command_id, _object(command)
                            )
                        )
            elif message.kind is WireMessageKind.envelope:
                envelope = TypeAdapter(QueuedEnvelope).validate_python(message.payload)
                result, response_kind = (
                    _object(await self.accept_envelope(envelope, identity)),
                    WireMessageKind.receipt,
                )
            elif message.kind is WireMessageKind.command_outcome:
                outcome = TypeAdapter(CommandOutcome).validate_python(message.payload)
                self._resolve_command(outcome)
                result, response_kind = (
                    {"command_id": outcome.command_id},
                    WireMessageKind.command_outcome_ack,
                )
            elif message.kind is WireMessageKind.read_request:
                read = TypeAdapter(LiveReadRequest).validate_python(message.payload)
                result, response_kind = await self._read(read), WireMessageKind.read_response
            elif message.kind in (
                WireMessageKind.file_manifest,
                WireMessageKind.file_chunk,
                WireMessageKind.file_complete,
            ):
                result, response_kind = await self._file(message, identity)
            else:
                raise ValueError("unsupported executor frame")
            if self.writer is writer and self.identity == identity:
                await self._send(WireMessage(response_kind, message.request_id, result))
        except (sqlite3.OperationalError, OSError):
            writer.close()
        except (ValueError, PlannerError, KeyError, TypeError) as error:
            if self.writer is writer:
                await self._send(
                    WireMessage(
                        WireMessageKind.error,
                        message.request_id,
                        error.to_payload()
                        if isinstance(error, PlannerError)
                        else {"error": {"code": "validation", "message": str(error)}},
                    )
                )

    async def _read(self, read: LiveReadRequest) -> JsonObject:
        self._assignment(read.child)
        if not read.path.startswith(
            (
                "/api/tickets",
                "/api/items",
                "/api/sprint",
                "/api/projects",
                "/api/worker-types",
                "/api/skills",
                "/files/sprint-items/",
            )
        ):
            raise ValueError("unsupported executor read")
        import httpx

        from planner.core.contracts import principal_legacy_actor

        principal = self._assignment(read.child)
        headers = {"X-Plan-Actor": principal_legacy_actor(principal)}
        if principal.kind is PrincipalKind.ticket:
            headers["X-Plan-Ticket-ID"] = principal.id
        elif principal.kind is PrincipalKind.sprint_item:
            headers["X-Plan-Sprint-Item-ID"] = principal.id
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://executor"
        ) as client:
            response = await client.get(read.path, params=cast(Any, read.params), headers=headers)
            try:
                value = response.json()
            except ValueError:
                return {"text": response.text}
            return value if isinstance(value, dict) else {"data": value}

    def _authorize_file(self, publication: FilePublicationRequest) -> None:
        principal = self._assignment(publication.child)
        manifest = publication.manifest
        if manifest.scope is FileScope.ticket:
            if principal.kind is not PrincipalKind.ticket or manifest.owner_id != principal.id:
                raise PlannerError(
                    ErrorCode.agent_forbidden, "artifact belongs to another Ticket", {}
                )
        elif manifest.owner_id != publication.child.conversation_id:
            raise PlannerError(
                ErrorCode.agent_forbidden, "input belongs to another conversation", {}
            )

    async def _file(
        self, message: WireMessage, identity: ExecutorIdentity
    ) -> tuple[JsonObject, WireMessageKind]:
        from planner.executor.publication import PublicationReceiver

        if self.file_receiver is None:
            root = Path(self.db_path).resolve().parent
            self.file_receiver = PublicationReceiver(
                self.store, root / "files", root / "executor-quarantine"
            )
        if message.kind is WireMessageKind.file_manifest:
            publication = TypeAdapter(FilePublicationRequest).validate_python(message.payload)
            self._authorize_file(publication)
            manifest = publication.manifest
            self._file_assignments[manifest.publication_id] = publication.child
            self.file_receiver.begin(manifest, identity=identity)
            accepted = self.store.publication_accepted(manifest)
            return _object(
                manifest
            ), WireMessageKind.file_accepted if accepted else WireMessageKind.file_pending
        if message.kind is WireMessageKind.file_chunk:
            chunk = TypeAdapter(FileChunk).validate_python(message.payload)
            if chunk.publication_id not in self._file_assignments:
                raise ValueError("file chunk has no authorized assignment")
            self._assignment(self._file_assignments[chunk.publication_id])
            self.file_receiver.write_chunk(chunk, identity=identity)
            return {}, WireMessageKind.file_chunk
        publication = TypeAdapter(FilePublicationRequest).validate_python(message.payload)
        self._authorize_file(publication)
        manifest = publication.manifest
        if self._file_assignments.get(manifest.publication_id) != publication.child:
            raise ValueError("file publication assignment changed")
        self.file_receiver.complete(manifest, now_ms(), identity=identity)
        return _object(manifest), WireMessageKind.file_accepted


def _object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded
