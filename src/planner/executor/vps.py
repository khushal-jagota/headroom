"""The VPS server endpoint and the SSH stdio relay's local Unix connection."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import BackendEventSink, BackendSpawnFailed
from planner.conversation.contracts import (
    MoveRetirementDisposition,
    MoveRetirementResult,
    MoveSourceRetirement,
    ResolvedConversationStart,
)
from planner.conversation.events import UserInputAnswer
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.executor.codec import encode_value
from planner.executor.contracts import (
    AcceptanceReceipt,
    AnswerApplication,
    AskKind,
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
    ExecutorHealthRead,
    ExecutorIdentity,
    FilePublicationRequest,
    FileScope,
    JsonObject,
    LiveReadRequest,
    QueuedEnvelope,
    ReconciliationInventory,
    ReconciliationResult,
    SourceRetirementReceipt,
    SourceRetirementRequest,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import snapshot_file
from planner.executor.mutations import write_ticket_mutation
from planner.executor.remote import RemoteBackendChild, RemoteBackendChildFactory
from planner.executor.sftp import RestrictedSftpService
from planner.executor.store import ExecutorStore
from planner.executor.wire import (
    parse_identity,
    read_frame,
    write_frame,
)
from planner.previews.forward import PreviewForwards


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


class _MoveSource:
    def __init__(
        self, endpoint: ExecutorEndpoint, ticket_id: str, conversation_id: str | None
    ) -> None:
        self.endpoint = endpoint
        self.ticket_id = ticket_id
        self.conversation_id = conversation_id
        self.identity: ExecutorIdentity | None = None
        self.writer: asyncio.StreamWriter | None = None

    def validate_current(self) -> None:
        endpoint = self.endpoint
        if (
            self.identity is None
            or not endpoint.ready
            or endpoint.identity != self.identity
            or endpoint.writer is not self.writer
            or self.writer is None
            or self.writer.is_closing()
        ):
            raise ConnectionError("Mac source connection changed during move")
        endpoint.store.require_ready_connection(self.identity)

    async def retire(self) -> MoveRetirementResult:
        endpoint = self.endpoint
        self.identity, self.writer = endpoint.identity, endpoint.writer
        try:
            self.validate_current()
        except (ConnectionError, PlannerError):
            return MoveRetirementResult(
                MoveRetirementDisposition.refused, "current Mac source cannot be verified"
            )
        assert self.identity is not None
        refs = endpoint.store.assigned_ticket_children(
            self.ticket_id, self.identity.executor_incarnation
        )
        ids = {ref.child_id for ref in refs}
        if any(
            command.child.child_id in ids and not future.done()
            for command, future in endpoint.pending_commands.values()
        ):
            return MoveRetirementResult(
                MoveRetirementDisposition.refused, "source command or input transfer is pending"
            )
        request = SourceRetirementRequest(self.identity, self.ticket_id, self.conversation_id, refs)
        try:
            response = await endpoint._request(WireMessageKind.source_retire, _object(request))
            if response.kind is not WireMessageKind.source_retired:
                raise ValueError("unexpected source retirement response")
            receipt = TypeAdapter(SourceRetirementReceipt).validate_python(response.payload)
            if (
                receipt.identity != request.expected_identity
                or receipt.ticket_id != request.ticket_id
                or receipt.conversation_id != request.conversation_id
                or receipt.assigned_children != request.assigned_children
            ):
                raise ValueError("source retirement response conflicts with request")
            # Keep a confirmed retirement distinct even when source replacement invalidates commit.
            if receipt.result.disposition is MoveRetirementDisposition.retired:
                for ref in refs:
                    endpoint.store.close_child(ref, now_ms())
                    endpoint.children.pop(ref.child_id, None)
                    endpoint.sinks.pop(ref.child_id, None)
            return receipt.result
        except (ConnectionError, OSError, ValueError, PlannerError):
            return MoveRetirementResult(
                MoveRetirementDisposition.uncertain, "source retirement outcome is unknown"
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
        self._preview_forwards: PreviewForwards | None = None
        self._server: asyncio.Server | None = None
        self.children: dict[str, RemoteBackendChild] = {}
        self.sinks: dict[str, BackendEventSink] = {}
        self.pending_commands: dict[str, tuple[ChildCommand, asyncio.Future[CommandOutcome]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._inventory: dict[str, ChildInventory] = {}
        self._retained_inventory: dict[str, CommandOutcome] = {}
        self._send_lock = asyncio.Lock()
        self._input_transfer_lock = asyncio.Lock()
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self.factory = RemoteBackendChildFactory(self.assign_child, self.send_command)
        self.file_receiver: Any = None
        self._responses: dict[str, asyncio.Future[WireMessage]] = {}
        self.clock_sample: ClockSample | None = None
        self.process_id: int | None = None
        self.observed_at_ms: int | None = None
        self.last_contact_at_ms: int | None = None
        self.lost_at_ms: int | None = None
        self._file_assignments: dict[str, ChildReference] = {}
        self._transports: dict[ExecutorIdentity, RestrictedSftpService] = {}
        self._transport_roots: dict[ExecutorIdentity, tempfile.TemporaryDirectory[str]] = {}
        self._upload_slots: dict[tuple[ExecutorIdentity, str], str] = {}
        self._transport_lock = asyncio.Lock()

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

    @asynccontextmanager
    async def preview_forward(self, port: int, ticket_id: str) -> AsyncIterator[int]:
        forwards = self._preview_forwards
        if not self.ready or forwards is None:
            raise ConnectionError("Mac executor is unavailable")
        async with forwards.lease(port, ticket_id) as local_port:
            yield local_port

    async def close(self) -> None:
        if self._preview_forwards is not None:
            await self._preview_forwards.close()
            self._preview_forwards = None
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

    def source_for_move(self, ticket_id: str, conversation_id: str | None) -> MoveSourceRetirement:
        return _MoveSource(self, ticket_id, conversation_id)

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
        async with self._input_transfer_lock:
            await self._transfer_command_inputs_locked(command)

    async def _transfer_command_inputs_locked(self, command: ChildCommand) -> None:
        identity = self.identity
        if identity is None or identity not in self._transports:
            raise ConnectionError("restricted SFTP unavailable")
        service = self._transports[identity]
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
                path = await asyncio.to_thread(
                    self.runtime.message_files.path_of,
                    command.child.conversation_id,
                    stored_file_id,
                )
                snapshot = await asyncio.to_thread(
                    snapshot_file,
                    root=path.parent,
                    relative_path=path.name,
                    scope=FileScope.conversation,
                    owner_id=command.child.conversation_id,
                )
                slot = None
                try:
                    self.store.verify_connection(identity)
                    slot = await service.create_download_async(snapshot)
                    response = await self._request(WireMessageKind.file_slot, _object(slot))
                    if (
                        response.kind is not WireMessageKind.file_accepted
                        or response.payload != _object(snapshot.manifest)
                    ):
                        raise ValueError("Mac did not confirm complete input bytes")
                    self.store.verify_connection(identity)
                finally:
                    if slot is not None:
                        service.release(slot.token)
                    snapshot.local_path.unlink(missing_ok=True)

    async def send_command(self, command: ChildCommand) -> CommandOutcome:
        existing = self.pending_commands.get(command.command_id)
        if existing is not None:
            if existing[0] != command:
                raise ValueError("command identity reused with different content")
            return await asyncio.shield(existing[1])
        future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        self.pending_commands[command.command_id] = (command, future)
        try:
            if self.ready and self.writer is not None:
                try:
                    try:
                        await self._transfer_command_inputs(command)
                    except (ConnectionError, ValueError):
                        # Keep the original waiting caller and stable command. A
                        # new connection retries its inputs before native dispatch.
                        if self.writer is not None:
                            self.writer.close()
                    else:
                        await self._send(
                            WireMessage(
                                WireMessageKind.command, command.command_id, _object(command)
                            )
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
        previous = self.store.preflight(envelope, identity=identity)
        if previous is not None:
            return previous
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
                    if (
                        event.method is BackendEventMethod.user_input_requested
                        and event.turn is not None
                    ):
                        from planner.executor.codec import decode_event_arguments

                        request = decode_event_arguments(event)["request"]
                        self.store.publish_user_input_request_in_transaction(
                            conn,
                            AskReference(
                                event.child, event.turn, request.request_id, AskKind.user_input
                            ),
                            request.questions,
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
            if write.child.conversation_id != envelope.conversation_id:
                raise ValueError("CLI conversation conflicts with envelope")
            if write.operation in ("message.send", "ticket.request_help"):
                from planner.executor.native_messages import bound_native_principal

                principal = bound_native_principal(self.store, envelope) or self._assignment(
                    write.child
                )
                return await self._native_message(envelope, write, principal, identity)
            principal = self._assignment(write.child)
            return self.store.accept(
                envelope,
                lambda conn, row: write_ticket_mutation(conn, write, principal, self.clock),
                now_ms(),
                identity=identity,
            )
        if envelope.kind is EnvelopeKind.command_outcome:
            outcome = TypeAdapter(CommandOutcome).validate_python(envelope.payload)
            if outcome.child.conversation_id != envelope.conversation_id:
                raise ValueError("command outcome conversation conflicts with envelope")
            retired_refusal = (
                outcome.disposition is CommandDisposition.refused
                and outcome.result
                == {
                    "error_type": "BackendSpawnFailed",
                    "error_message": "source child identity was retired",
                }
                and outcome.child.executor_incarnation == identity.executor_incarnation
                and self.store.has_closed_child_assignment(outcome.child)
            )
            if not retired_refusal:
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
        from planner.executor.native_messages import deliver_native_message

        return await deliver_native_message(
            store=self.store,
            envelope=envelope,
            write=write,
            principal=principal,
            identity=identity,
            clock=self.clock,
            conversations=self.runtime.system,
            resolve_recipient=self._resolve_native_recipient,
        )

    def _resolve_native_recipient(
        self, conn: sqlite3.Connection, write: CliWrite, principal: Principal
    ) -> Principal:
        from planner.message_delivery import api

        body = write.arguments.get("body")
        if not isinstance(body, dict):
            raise PlannerError(ErrorCode.validation, "message body must be an object", {})
        if write.operation == "message.send":
            return api._principal(body.get("target"))
        default = self._help_recipient(conn, write, principal)
        if set(body) - {"message", "recipient"}:
            raise PlannerError(ErrorCode.validation, "unknown help request field", {})
        return api._principal(body["recipient"]) if body.get("recipient") else default

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

    def health(self) -> ExecutorHealthRead:
        return {
            "identity": None if self.identity is None else _object(self.identity),
            "process_id": self.process_id,
            "observed_at_ms": self.observed_at_ms,
            "lost_at_ms": self.lost_at_ms,
            "last_contact_at_ms": self.last_contact_at_ms,
            "last_contact_source": "vps_received_valid_executor_frame",
            "connection_state": "ready"
            if self.ready
            else "disconnected"
            if self.writer is None
            else "reconciling",
            "reconciliation_ready": self.ready,
        }

    def owns_conversation(self, conversation_id: str) -> bool:
        return any(
            child.reference.conversation_id == conversation_id for child in self.children.values()
        )

    def conversation_asks(self, conversation_id: str) -> list[JsonObject]:
        result: list[JsonObject] = []
        for evidence in self.store.evidence_for_conversation(conversation_id):
            answer = self.store.recorded_answer(evidence.ask)
            timing = self.store.action_timing(evidence.ask)
            result.append(
                {
                    "evidence": _object(evidence),
                    "answer": None if answer is None else _object(answer),
                    "answer_action_timing": None if timing is None else _object(timing),
                }
            )
        return result

    def conversation_context(self, conversation_id: str) -> JsonObject | None:
        """Current remote child and turn, independent of historical ask records."""
        state = self.runtime.system._conversations.get(conversation_id)
        if state is None or not isinstance(state.child, RemoteBackendChild):
            return None
        return {
            "child": _object(state.child.reference),
            "turn": None if state.running_turn is None else _object(state.running_turn.token),
        }

    def _current_ask_evidence(
        self, conversation_id: str, ask_id: str, kind: AskKind = AskKind.permission
    ) -> Any:
        state = self.runtime.system._conversations.get(conversation_id)
        running = None if state is None else state.running_turn
        if running is None or not isinstance(state.child, RemoteBackendChild):
            return None
        for evidence in reversed(self.store.evidence_for_conversation(conversation_id)):
            if (
                evidence.ask.ask_id == ask_id
                and evidence.ask.child == state.child.reference
                and evidence.ask.turn == running.token
                and evidence.ask.kind is kind
            ):
                return evidence
        return None

    def ask_status(
        self, conversation_id: str, ask_id: str, kind: AskKind = AskKind.permission
    ) -> JsonObject:
        evidence = self._current_ask_evidence(conversation_id, ask_id, kind)
        if evidence is None:
            return {"answer_available": False, "executor_evidence": None}
        return {
            "executor_evidence": _object(evidence),
            "answer_available": self.ready
            and self.identity is not None
            and evidence.connection_generation == self.identity.connection_generation
            and evidence.ask.child.executor_incarnation == self.identity.executor_incarnation
            and evidence.reachability.value == "reachable"
            and evidence.answer_id is None,
        }

    async def answer_permission(self, conversation_id: str, ask_id: str, option_id: str) -> bool:
        from planner.executor.contracts import ChildOperation

        if not self.ready or self.identity is None:
            return False
        evidence = self._current_ask_evidence(conversation_id, ask_id)
        if evidence is not None:
            try:
                answer = self.store.choose_answer(
                    evidence.ask, option_id, self.identity.connection_generation, now_ms()
                )
            except PlannerError:
                return False
            # The selection is durable before any native attempt. A timeout or
            # drop keeps it in the store for reconciliation, never permits replacement.
            command = ChildCommand(
                "apply_" + answer.answer_id,
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

    async def answer_user_input(
        self, conversation_id: str, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> bool:
        from planner.executor.contracts import ChildOperation

        if not self.ready or self.identity is None:
            return False
        evidence = self._current_ask_evidence(conversation_id, request_id, AskKind.user_input)
        if evidence is None:
            return False
        try:
            answer = self.store.choose_user_input_answer(
                evidence.ask, answers, self.identity.connection_generation, now_ms()
            )
        except PlannerError:
            return False
        command = ChildCommand(
            "apply_" + answer.answer_id,
            evidence.ask.child,
            ChildOperation.answer_user_input,
            evidence.ask.turn,
            {"answer": _object(answer)},
        )
        task = asyncio.create_task(self.send_command(command))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        identity = None
        forwards: PreviewForwards | None = None
        try:
            hello = await read_frame(reader)
            if hello.kind is not WireMessageKind.hello:
                raise ValueError("executor handshake required")
            parsed_identity = parse_identity(hello.payload.get("identity"))
            health = TypeAdapter(ExecutorHealth).validate_python(hello.payload)
            if health.identity != parsed_identity:
                raise ValueError("identity coercion refused")
            if type(hello.payload.get("process_id")) is not int or health.process_id < 1:
                raise ValueError("invalid executor process identity")
            identity = health.identity
            old_identity = self.identity
            old_writer = self.writer
            self.store.begin_connection(identity, now_ms())
            self.process_id = health.process_id
            self.observed_at_ms = now_ms()
            self.lost_at_ms = None
            old_previews = self._preview_forwards
            self.identity, self.writer, self.ready = identity, writer, False
            self.last_contact_at_ms = now_ms()

            self._preview_forwards = None
            if old_writer is not None and old_writer is not writer:
                old_writer.close()
            if old_previews is not None:
                await old_previews.close()
            if old_identity is not None:
                await self._close_transport(old_identity)
            if self.writer is not writer or self.identity != identity:
                return
            self._inventory = {}
            self._retained_inventory = {}
            if (
                old_identity is not None
                and old_identity.executor_incarnation != identity.executor_incarnation
            ):
                for command, future in tuple(self.pending_commands.values()):
                    if not future.done():
                        future.set_result(
                            CommandOutcome(
                                command.command_id,
                                command.child,
                                CommandDisposition.uncertain,
                                {"reason": "executor_process_replaced"},
                            )
                        )
            async with self._send_lock:
                if self.writer is not writer or self.identity != identity:
                    return
                await write_frame(
                    writer,
                    WireMessage(
                        WireMessageKind.hello_accepted, hello.request_id, _object(identity)
                    ),
                )
            if self.writer is not writer or self.identity != identity:
                return
            probe = asyncio.create_task(self._measure_clock())
            self._tasks.add(probe)
            probe.add_done_callback(self._tasks.discard)
            while self.writer is writer and self.identity == identity:
                message = await read_frame(reader)
                # Decode and fence synchronously before domain work can yield.
                # A valid frame proves receipt even when its operation is refused.
                if self.writer is not writer or self.identity != identity:
                    break
                self.last_contact_at_ms = now_ms()
                task = asyncio.create_task(self._handle(message, identity, writer))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            pass
        finally:
            if identity is not None and self.writer is writer and self.identity == identity:
                # Detach this generation before cleanup yields to a replacement hello.
                self.ready = False
                forwards = self._preview_forwards
                self._preview_forwards = None
                self.writer = None
                self.lost_at_ms = now_ms()
                self.store.mark_lost(identity, now_ms())
                for response_future in self._responses.values():
                    if not response_future.done():
                        response_future.set_exception(ConnectionError("executor link lost"))
            writer.close()
            current_forwards = self._preview_forwards if self.identity == identity else None
            if forwards is not None:
                await forwards.close()
            if current_forwards is not None:
                await current_forwards.close()
            if identity is not None:
                await self._close_transport(identity)

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
            if message.kind is WireMessageKind.transport_open:
                async with self._transport_lock:
                    if (
                        identity in self._transports
                        or parse_identity(message.payload.get("identity")) != identity
                    ):
                        raise ValueError("duplicate or stale transport generation")
                    root = tempfile.TemporaryDirectory(prefix="panels-ssh-")
                    root_path = Path(root.name)
                    previews_root = root_path / "p"
                    previews_root.mkdir(mode=0o700)
                    service = RestrictedSftpService(
                        identity,
                        root_path / "s",
                        str(message.payload["client_public_key"]),
                        lambda: self.identity == identity and self.writer is writer,
                    )
                    try:
                        endpoint = await service.start()
                        self.store.verify_connection(identity)
                    except BaseException:
                        await service.close()
                        root.cleanup()
                        raise
                    self._transports[identity] = service
                    self._transport_roots[identity] = root

                    async def preview_request(
                        kind: WireMessageKind, payload: JsonObject
                    ) -> WireMessage:
                        if self.identity != identity or self.writer is not writer:
                            raise ConnectionError("preview generation changed")
                        return await self._request(kind, payload)

                    self._preview_forwards = PreviewForwards(
                        previews_root,
                        preview_request,
                        lambda: self.identity == identity and self.writer is writer and self.ready,
                    )
                    result, response_kind = (
                        {"endpoint": _object(endpoint), "preview_root": str(previews_root)},
                        WireMessageKind.transport_opened,
                    )
            elif message.kind is WireMessageKind.reconcile_inventory:
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
                    self._retained_inventory[outcome.command_id] = outcome
                    self._resolve_command(outcome)
                if not inventory.children and not inventory.retained_command_outcomes:
                    from planner.executor.contracts import ChildOperation

                    merged = replace(
                        inventory,
                        children=tuple(self._inventory.values()),
                        retained_command_outcomes=tuple(self._retained_inventory.values()),
                    )
                    reconciliation = self.store.reconcile(merged, now_ms())
                    # Evidence stays canonical on the VPS. Each pending answer is
                    # a separate bounded stable command, never a growing reply frame.
                    for answer in reconciliation.answers:
                        command = ChildCommand(
                            "apply_" + answer.answer_id,
                            answer.ask.child,
                            ChildOperation.answer_user_input
                            if answer.ask.kind is AskKind.user_input
                            else ChildOperation.answer_permission_ask,
                            answer.ask.turn,
                            {"answer": _object(answer)},
                        )
                        task = asyncio.create_task(self.send_command(command))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                    for command_id in reconciliation.acknowledged_command_ids:
                        await self._send(
                            WireMessage(
                                WireMessageKind.command_outcome_ack,
                                command_id,
                                {"command_id": command_id},
                            )
                        )
                    result = _object(
                        ReconciliationResult(identity, reconciliation.receipts, (), (), ())
                    )
                    self._inventory = {}
                    self._retained_inventory = {}
                else:
                    result = _object(ReconciliationResult(identity, (), (), (), ()))
                response_kind = WireMessageKind.reconcile_result
            elif message.kind is WireMessageKind.reconciliation_ready:
                self.ready = True
                if hasattr(self.store, "mark_ready"):
                    self.store.mark_ready(identity, now_ms())
                await self.runtime.system.resume_executor_held_prompts(remote_ready=True)
                result, response_kind = {"ready": True}, WireMessageKind.reconciliation_ready
                # Resending a stable command to the same live registry is safe:
                # the registry retains its pending task and first outcome.
                for command, future in tuple(self.pending_commands.values()):
                    if (
                        not future.done()
                        and command.child.executor_incarnation == identity.executor_incarnation
                    ):
                        await self._transfer_command_inputs(command)
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
                "/api/feedback",
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
        service = self._transports.get(identity)
        if service is None:
            raise ValueError("restricted SFTP generation unavailable")
        publication = TypeAdapter(FilePublicationRequest).validate_python(message.payload)
        self._authorize_file(publication)
        manifest = publication.manifest
        key = identity, manifest.publication_id
        if message.kind is WireMessageKind.file_manifest:
            self._file_assignments[manifest.publication_id] = publication.child
            await asyncio.to_thread(self.file_receiver.begin, manifest, identity=identity)
            self.store.verify_connection(identity)
            if self.store.publication_accepted(manifest):
                return _object(manifest), WireMessageKind.file_accepted
            previous = self._upload_slots.pop(key, None)
            if previous is not None:
                service.release(previous)
            slot = service.create_upload(manifest)
            self._upload_slots[key] = slot.token
            return _object(slot), WireMessageKind.file_slot
        if self._file_assignments.get(manifest.publication_id) != publication.child:
            raise ValueError("file publication assignment changed")
        token = self._upload_slots.pop(key, None)
        if token is None:
            if self.store.publication_accepted(manifest):
                return _object(manifest), WireMessageKind.file_accepted
            raise ValueError("unknown publication slot")
        sealed = None
        try:
            sealed = await service.seal_upload(token)
            self.store.verify_connection(identity)
            self._authorize_file(publication)
            await asyncio.to_thread(
                self.file_receiver.ingest_sealed, manifest, sealed, identity=identity
            )
            self.store.verify_connection(identity)
            await asyncio.to_thread(
                self.file_receiver.complete, manifest, now_ms(), identity=identity
            )
            self.store.verify_connection(identity)
            return _object(manifest), WireMessageKind.file_accepted
        finally:
            if sealed is not None:
                sealed.unlink(missing_ok=True)
            service.release(token)

    async def _close_transport(self, identity: ExecutorIdentity) -> None:
        service = self._transports.pop(identity, None)
        if service is not None:
            await service.close()
        for key in tuple(self._upload_slots):
            if key[0] == identity:
                self._upload_slots.pop(key)
        root = self._transport_roots.pop(identity, None)
        if root is not None:
            root.cleanup()


def _object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded
