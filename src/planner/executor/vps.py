"""The executor endpoint attached directly to the app WebSocket service."""

from __future__ import annotations

import asyncio
import json
import logging
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

from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendEventSink,
    BackendSpawnFailed,
)
from planner.conversation.contracts import (
    MoveRetirementDisposition,
    MoveRetirementResult,
    MoveSourceRetirement,
    ResolvedConversationStart,
)
from planner.conversation.events import UserInputAnswer
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogObservation,
    CatalogRequest,
    CatalogResponse,
    CatalogResult,
    CatalogUnavailable,
    CorrectiveAction,
    LaunchContext,
    ValidationReason,
)
from planner.core import db
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.executor.codec import encode_value
from planner.executor.configuration import (
    CATALOG_CANCEL_TIMEOUT_SECONDS,
    CATALOG_TIMEOUT_SECONDS,
    INPUT_CANCEL_TIMEOUT_SECONDS,
    INPUT_PROGRESS_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
)
from planner.executor.contracts import (
    AcceptanceReceipt,
    AnswerApplication,
    AskKind,
    AskReference,
    BackendEvent,
    BackendEventMethod,
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    ChildReference,
    CliWrite,
    ClockSample,
    CommandDisposition,
    CommandOutcome,
    DurableAnswer,
    EnvelopeKind,
    ExecutorHealth,
    ExecutorHealthRead,
    ExecutorIdentity,
    FilePublicationRequest,
    FileScope,
    FrameReader,
    FrameWriter,
    InputTransferCancel,
    InputTransferCancelled,
    InputTransferFailed,
    InputTransferProgress,
    InputTransferRequest,
    JsonObject,
    LiveReadRequest,
    QueuedEnvelope,
    ReconciliationInventory,
    ReconciliationResult,
    RemoteInputTransferFailed,
    SourceRetirementReceipt,
    SourceRetirementRequest,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import snapshot_file
from planner.executor.input_transfers import InputTransferDeadline, InputTransferExchange
from planner.executor.mutations import write_ticket_mutation
from planner.executor.remote import (
    RemoteBackendChild,
    RemoteBackendChildFactory,
    RemoteInitialDeliveryCancelled,
)
from planner.executor.skills import read_canonical_skill_snapshot
from planner.executor.store import ExecutorStore
from planner.executor.transfers import GenerationTransfers, wait_for_settlement
from planner.executor.wire import (
    parse_identity,
    read_frame,
    write_frame,
)
from planner.previews.forward import PreviewForwards
from planner.tickets.contracts import TicketLocale


def _generation_root() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="panels-executor-")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class _MoveSource:
    def __init__(
        self, endpoint: ExecutorEndpoint, ticket_id: str, conversation_id: str | None
    ) -> None:
        self.endpoint = endpoint
        self.ticket_id = ticket_id
        self.conversation_id = conversation_id
        self.identity: ExecutorIdentity | None = None
        self.writer: FrameWriter | None = None

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
        self.writer: FrameWriter | None = None
        self.ready = False
        self._preview_forwards: PreviewForwards | None = None
        self.children: dict[str, RemoteBackendChild] = {}
        self.sinks: dict[str, BackendEventSink] = {}
        self.pending_commands: dict[str, tuple[ChildCommand, asyncio.Future[CommandOutcome]]] = {}
        self._cancelled_initial_children: set[ChildReference] = set()
        self._possibly_emitted_commands: set[str] = set()
        self._newly_recorded_commands: dict[str, ChildCommand] = {}
        self._policy_held_commands: set[str] = set()
        self._revoked_input_commands: set[str] = set()
        self._prompt_command_ids: set[str] = set()
        self._emission_tasks: dict[str, set[asyncio.Task[bool]]] = {}
        self._input_tasks: dict[str, set[asyncio.Task[None]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._inventory: dict[str, ChildInventory] = {}
        self._retained_inventory: dict[str, CommandOutcome] = {}
        self._send_lock = asyncio.Lock()
        self._input_transfer_locks: dict[str, asyncio.Lock] = {}
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self.factory = RemoteBackendChildFactory(
            self.assign_child,
            self.send_new_command,
            self._read_skill_snapshot,
            self.cancel_initial_delivery,
            self.retire_child,
        )
        self.file_receiver: Any = None
        self._input_exchanges: dict[str, InputTransferExchange] = {}
        self._responses: dict[str, asyncio.Future[WireMessage]] = {}
        self._catalog_responses: dict[
            str, tuple[CatalogRequest, FrameWriter, asyncio.Future[WireMessage]]
        ] = {}
        self.clock_sample: ClockSample | None = None
        self.process_id: int | None = None
        self.observed_at_ms: int | None = None
        self.last_contact_at_ms: int | None = None
        self.lost_at_ms: int | None = None
        self._file_assignments: dict[str, ChildReference] = {}
        self._transports: dict[ExecutorIdentity, GenerationTransfers] = {}
        self._transport_roots: dict[ExecutorIdentity, tempfile.TemporaryDirectory[str]] = {}
        self._upload_transfers: dict[tuple[ExecutorIdentity, str], str] = {}
        self._transport_lock = asyncio.Lock()
        self._receiver_work: dict[ExecutorIdentity, set[asyncio.Task[Any]]] = {}

    async def start(self) -> None:
        """The app router owns the listener; no separate relay socket exists."""

    @asynccontextmanager
    async def preview_forward(self, port: int, ticket_id: str) -> AsyncIterator[int]:
        forwards = self._preview_forwards
        if not self.ready or forwards is None:
            raise ConnectionError("Mac executor is unavailable")
        async with forwards.lease(port, ticket_id) as local_port:
            yield local_port

    async def close(self) -> None:
        # Revoke admission synchronously before any preview or file cleanup yields.
        self.ready = False
        writer, self.writer = self.writer, None
        if writer is not None:
            writer.close()
        self._fail_catalogs()
        for exchange in self._input_exchanges.values():
            if not exchange.response.done():
                exchange.response.set_exception(ConnectionError("executor app stopped"))
        for future in self._responses.values():
            if not future.done():
                future.set_exception(ConnectionError("executor app stopped"))
        previews, self._preview_forwards = self._preview_forwards, None
        if previews is not None:
            await previews.close()
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for identity in tuple(self._transports):
            await self._close_transport(identity)
        if writer is not None:
            await writer.wait_closed()

    def source_for_move(self, ticket_id: str, conversation_id: str | None) -> MoveSourceRetirement:
        return _MoveSource(self, ticket_id, conversation_id)

    def _read_skill_snapshot(self, start: ResolvedConversationStart) -> CanonicalSkillSnapshot:
        environment = dict(
            start.role_materials.identity_environment_variables if start.role_materials else ()
        )
        ticket_id = environment.get("PLAN_TICKET_ID")
        if environment.get("PLAN_ACTOR") != "worker" or not ticket_id:
            raise BackendSpawnFailed("Canonical skill bootstrap requires a Worker Ticket identity")
        conn = db.connect(self.db_path)
        try:
            conn.execute("BEGIN")
            return read_canonical_skill_snapshot(conn, ticket_id)
        finally:
            conn.close()

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
        elif actor in {"manager", "sprint_item_supervisor"}:
            from planner.core.authctx import normalize_manager_identity

            manager_id = normalize_manager_identity(
                env.get("PLAN_MANAGER_ID"), env.get("PLAN_SPRINT_ITEM_ID")
            )
            if manager_id is None:
                raise BackendSpawnFailed("remote Manager needs its assigned identity")
            principal = Principal(PrincipalKind.manager, manager_id)
        else:
            raise BackendSpawnFailed("remote children need a server-assigned Worker identity")
        self.store.register_child(child, principal.kind.value, principal.id, now_ms())
        self.sinks[child.child_id] = sink
        proxy = self.recover_child(child, initial_history_complete=True)
        self.children[child.child_id] = proxy
        return child

    def recover_child(
        self, child: ChildReference, *, initial_history_complete: bool = False
    ) -> RemoteBackendChild:
        """Bind the retained identity without starting it or granting new authority."""
        return RemoteBackendChild(
            child,
            self.send_new_command,
            self._read_skill_snapshot,
            self.cancel_initial_delivery,
            initial_history_complete=initial_history_complete,
            retire_reference=self.retire_child,
        )

    def retire_child(self, child: ChildReference) -> None:
        """Drop only the exact retired proxy and its reconciliation material."""
        proxy = self.children.get(child.child_id)
        if proxy is not None and proxy.reference == child:
            self.children.pop(child.child_id, None)
            self.sinks.pop(child.child_id, None)
        item = self._inventory.get(child.child_id)
        if item is not None and item.child == child:
            self._inventory.pop(child.child_id, None)

    def _outcome_requires_receipt(self, outcome: CommandOutcome) -> bool:
        pending = self.pending_commands.get(outcome.command_id)
        return (
            pending is not None
            and pending[0].child == outcome.child
            and (
                pending[0].operation is ChildOperation.stop
                or "admission_expectation" in pending[0].arguments
            )
        ) or self.store.is_retirement_outcome(outcome)

    def _outcome_hint_acknowledged(self, outcome: CommandOutcome) -> bool:
        return not self._outcome_requires_receipt(
            outcome
        ) or self.store.has_accepted_command_outcome(outcome)

    def _initial_command_cancelled(self, command: ChildCommand) -> bool:
        return command.command_id in self._revoked_input_commands or (
            command.operation in (ChildOperation.start, ChildOperation.write_prompt)
            and command.child in self._cancelled_initial_children
        )

    def cancel_initial_delivery(self, child: ChildReference, command_ids: tuple[str, ...]) -> bool:
        """Fence before cleanup; retained native outcomes remain untouched."""
        self._cancelled_initial_children.add(child)
        prompt_may_have_sent = any(
            command_id in self._possibly_emitted_commands
            for command_id in command_ids
            if command_id in self._prompt_command_ids
        )
        for command, future in tuple(self.pending_commands.values()):
            if command.child != child or not self._initial_command_cancelled(command):
                continue
            if command.operation is ChildOperation.write_prompt:
                prompt_may_have_sent |= command.command_id in self._possibly_emitted_commands
            for task in self._input_tasks.get(command.command_id, ()):
                task.cancel()
            for emission in self._emission_tasks.get(command.command_id, ()):
                emission.cancel()
            if not future.done() and command.command_id not in self._possibly_emitted_commands:
                future.set_exception(RemoteInitialDeliveryCancelled())
        return prompt_may_have_sent

    async def _send(self, message: WireMessage) -> None:
        writer = self.writer
        async with self._send_lock:
            if writer is None or self.writer is not writer:
                raise ConnectionError("executor link unavailable")
            await write_frame(writer, message)

    def _reconciliation_current(self, identity: ExecutorIdentity, writer: FrameWriter) -> bool:
        if self.writer is not writer or self.identity != identity:
            return False
        try:
            self.store.verify_connection(identity)
        except PlannerError:
            return False
        return True

    async def _send_reconciliation(
        self, message: WireMessage, identity: ExecutorIdentity, writer: FrameWriter
    ) -> bool:
        # This handler owns a particular connection, including while waiting for
        # another sender or draining an already written frame.
        async with self._send_lock:
            if not self._reconciliation_current(identity, writer):
                return False
            await write_frame(writer, message)
            return self._reconciliation_current(identity, writer)

    async def _request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        request_id = str(uuid4())
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        writer = self.writer
        deadline: asyncio.Timeout | None = None
        try:
            deadline = asyncio.timeout(REQUEST_TIMEOUT_SECONDS)
            async with deadline:
                await self._send(WireMessage(kind, request_id, payload))
                return await future
        except TimeoutError:
            if writer is not None:
                writer.close()
            raise
        finally:
            self._responses.pop(request_id, None)

    def _fail_catalogs(self, writer: FrameWriter | None = None) -> None:
        for _, captured_writer, future in self._catalog_responses.values():
            if (writer is None or writer is captured_writer) and not future.done():
                future.set_exception(ConnectionError("executor catalog connection lost"))

    async def observe_catalog(
        self,
        request: CatalogRequest,
        *,
        attempt_id: str,
        deadline: float | None = None,
    ) -> CatalogResult:
        """Read scoped metadata without child authority or transport timeout effects."""
        identity, writer = self.identity, self.writer
        failure_reason = ValidationReason.catalog_unavailable
        if not attempt_id:
            raise ValueError("catalog attempt ID must be nonempty")
        if (
            identity is None
            or writer is None
            or not self.ready
            or request.expected_context.executor_identity != identity
        ):
            return self._catalog_failure(request, ValidationReason.offline)
        request_id = str(uuid4())
        future: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        self._catalog_responses[request_id] = (request, writer, future)
        expires = asyncio.get_running_loop().time() + CATALOG_TIMEOUT_SECONDS
        if deadline is not None:
            expires = min(expires, deadline)
        completed = False
        try:
            async with asyncio.timeout_at(expires):
                async with self._send_lock:
                    if self.writer is not writer or self.identity != identity or not self.ready:
                        raise ConnectionError("catalog generation changed")
                    await write_frame(
                        writer,
                        WireMessage(WireMessageKind.catalog_request, request_id, _object(request)),
                    )
                response = await future
                if self.writer is not writer or self.identity != identity or not self.ready:
                    raise ConnectionError("catalog generation changed")
                if response.kind is not WireMessageKind.catalog_response:
                    raise ValueError("unexpected catalog reply")
                self._catalog_raw_identities(response.payload)
                parsed = TypeAdapter(CatalogResponse).validate_json(
                    json.dumps(response.payload), strict=True
                )
                if parse_identity(response.payload.get("executor_identity")) != identity:
                    raise ValueError("catalog reply identity changed")
                failure_reason = ValidationReason.context_changed
                if (
                    parsed.backend_key != request.backend_key
                    or parsed.executor_identity != identity
                ):
                    raise ValueError("catalog reply backend changed")
                result = self._catalog_result(parsed.result, request, request_id, attempt_id)
                completed = True
                return result
        except TimeoutError:
            failure_reason = ValidationReason.timeout
        except (ConnectionError, OSError):
            failure_reason = ValidationReason.offline
        except (ValueError, TypeError, KeyError):
            pass
        finally:
            self._catalog_responses.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
            if not completed:
                # Cancellation is a bounded read-only frame on the original
                # writer. A lost frame leaves the native deadline in force.
                task = asyncio.create_task(
                    self._cancel_catalog(
                        request_id,
                        request,
                        identity,
                        writer,
                    )
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return self._catalog_failure(request, failure_reason)

    def _catalog_failure(
        self, request: CatalogRequest, reason: ValidationReason
    ) -> CatalogUnavailable:
        return CatalogUnavailable(
            backend_key=request.backend_key,
            destination=TicketLocale.macos,
            attempted_context=None,
            reason=reason,
            corrective_action=CorrectiveAction.reconnect_destination
            if reason is ValidationReason.offline
            else CorrectiveAction.refresh_catalog,
            detail="The destination catalog could not be verified.",
            observed_at=int(time.time()),
            retained_evidence=None,
        )

    @staticmethod
    def _catalog_raw_identities(payload: JsonObject) -> None:
        # Existing ExecutorIdentity predates extra="forbid". Validate the raw
        # dictionaries before a dataclass parser can discard an unknown field.
        parse_identity(payload.get("executor_identity"))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("catalog result must be an object")
        contexts: list[object] = []
        if result.get("kind") == "observation":
            evidence = result.get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError("catalog evidence must be an object")
            contexts.append(evidence.get("context"))
        elif result.get("kind") == "unavailable":
            if result.get("attempted_context") is not None:
                contexts.append(result["attempted_context"])
            retained = result.get("retained_evidence")
            if retained is not None:
                if not isinstance(retained, dict):
                    raise ValueError("retained catalog evidence must be an object")
                contexts.append(retained.get("context"))
        for context in contexts:
            if not isinstance(context, dict):
                raise ValueError("catalog context must be an object")
            parse_identity(context.get("executor_identity"))
            generation = context.get("account_generation")
            if generation is not None:
                if (
                    not isinstance(generation, dict)
                    or set(generation) != {"account_id", "last_refresh"}
                    or any(type(value) is not str or not value for value in generation.values())
                ):
                    raise ValueError("invalid catalog account generation fields")

    def _catalog_context(self, context: LaunchContext, request: CatalogRequest) -> None:
        if (
            context.backend_key != request.backend_key
            or context.destination is not TicketLocale.macos
            or context.executor_identity != request.expected_context.executor_identity
            or context.workspace != request.expected_context.workspace
            or not context.localized_workspace_folder.is_absolute()
        ):
            raise ValueError("catalog context differs from request")
        # Nested identity uses the same strict field and version validation as hello.
        if parse_identity(_object(context.executor_identity)) != self.identity:
            raise ValueError("catalog nested identity differs from connection")
        workspace = context.workspace
        from planner.conversation.contracts import WorkspacePathPolicy

        if workspace.workspace_path_policy is WorkspacePathPolicy.native_absolute:
            if workspace.workspace_home_relative is not None:
                raise ValueError("native catalog workspace cannot use home mapping")
        elif workspace.workspace_home_relative is not None:
            relative = Path(workspace.workspace_home_relative)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe catalog workspace mapping")
        # The authenticated native observer resolves symlinks and directory
        # existence with the same helper as execution. VPS cannot resolve that
        # host's paths or infer its home from a returned canonical directory.

    def _catalog_result(
        self, result: CatalogResult, request: CatalogRequest, request_id: str, attempt_id: str
    ) -> CatalogResult:
        if isinstance(result, CatalogObservation):
            self._catalog_context(result.evidence.context, request)
            if result.evidence.attempt_id != request_id:
                raise ValueError("catalog attempt differs from request")
            uncertainty: float | None = None
            sample = self.clock_sample
            if sample is not None and result.evidence.clock_uncertainty_seconds is not None:
                lower = sample.mac_sent_at_ms - sample.vps_received_at_ms
                upper = sample.mac_received_at_ms - sample.vps_sent_at_ms
                if (
                    sample.vps_received_at_ms >= sample.vps_sent_at_ms
                    and sample.mac_sent_at_ms >= sample.mac_received_at_ms
                    and lower <= upper
                ):
                    uncertainty = (
                        result.evidence.clock_uncertainty_seconds
                        + max(abs(lower), abs(upper)) / 1000
                        + 1.0
                    )
            return replace(
                result,
                evidence=replace(
                    result.evidence,
                    attempt_id=attempt_id,
                    clock_uncertainty_seconds=uncertainty,
                ),
            )
        if (
            result.backend_key != request.backend_key
            or result.destination is not TicketLocale.macos
        ):
            raise ValueError("catalog failure differs from request")
        if result.attempted_context is not None:
            self._catalog_context(result.attempted_context, request)
        if result.retained_evidence is not None:
            self._catalog_context(result.retained_evidence.context, request)
        return result

    async def _cancel_catalog(
        self,
        request_id: str,
        request: CatalogRequest,
        identity: ExecutorIdentity,
        writer: FrameWriter,
    ) -> None:
        try:
            async with asyncio.timeout(CATALOG_CANCEL_TIMEOUT_SECONDS):
                async with self._send_lock:
                    current = self.writer is writer and self.identity == identity and self.ready
                    if current:
                        await write_frame(
                            writer,
                            WireMessage(
                                WireMessageKind.catalog_cancel, request_id, _object(request)
                            ),
                        )
        except (ConnectionError, OSError, ValueError, TimeoutError):
            pass

    async def _input_exchange(self, request: InputTransferRequest) -> None:
        identity, writer = self.identity, self.writer
        if identity is None or writer is None:
            raise ConnectionError("input generation unavailable")
        request_id = str(uuid4())
        state = InputTransferExchange(
            InputTransferDeadline(request),
            identity,
            writer,
            asyncio.get_running_loop().create_future(),
        )
        self._input_exchanges[request_id] = state
        timer = None
        try:
            # Only slot admission retains the ordinary control timeout behavior.
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    if not await self._send_reconciliation(
                        WireMessage(WireMessageKind.file_slot, request_id, _object(request)),
                        identity,
                        writer,
                    ):
                        raise ConnectionError("input generation changed")
            except TimeoutError:
                writer.close()
                raise
            if state.deadline.phase == "download" and state.deadline.byte_count == 0:
                state.deadline.deadline = (
                    asyncio.get_running_loop().time() + INPUT_PROGRESS_TIMEOUT_SECONDS
                )
            timer = asyncio.create_task(state.deadline.expired())
            done, _ = await asyncio.wait(
                (state.response, timer), return_when=asyncio.FIRST_COMPLETED
            )
            if state.response not in done:
                raise RemoteInputTransferFailed("input transfer deadline expired")
            response = state.response.result()
            if not state.deadline.active():
                raise RemoteInputTransferFailed("input transfer deadline expired")
            if response.kind is not WireMessageKind.file_accepted or response.payload != _object(
                request.slot.manifest
            ):
                raise RemoteInputTransferFailed("Mac did not confirm complete input bytes")
        except (RemoteInputTransferFailed, asyncio.CancelledError) as error:
            state.deadline.revoked = True
            self._revoked_input_commands.add(request.command_id)
            if isinstance(error, asyncio.CancelledError):
                self._revoke_command_input(request.command_id)
            try:
                async with asyncio.timeout(INPUT_CANCEL_TIMEOUT_SECONDS):
                    await self._send_reconciliation(
                        WireMessage(
                            WireMessageKind.input_cancel,
                            request_id,
                            _object(
                                InputTransferCancel(request.command_id, request.slot.transfer_id)
                            ),
                        ),
                        identity,
                        writer,
                    )
                    await state.cancelled.wait()
            except TimeoutError:
                pass  # Logical revocation stands; remote cleanup is unconfirmed.
            except OSError as error:
                writer.close()
                raise ConnectionError("input cancellation connection failed") from error
            raise
        except OSError as error:
            writer.close()
            raise ConnectionError("input exchange connection failed") from error
        finally:
            state.deadline.revoked = True
            self._input_exchanges.pop(request_id, None)
            if state.response.done() and not state.response.cancelled():
                state.response.exception()
            else:
                state.response.cancel()
            if timer is not None:
                timer.cancel()
                await asyncio.gather(timer, return_exceptions=True)

    def _receive_input_frame(
        self, message: WireMessage, identity: ExecutorIdentity, writer: FrameWriter
    ) -> bool:
        state = self._input_exchanges.get(message.request_id)
        kinds = (
            WireMessageKind.input_progress,
            WireMessageKind.input_cancelled,
            WireMessageKind.input_failed,
        )
        if state is None:
            return message.kind in kinds
        if state.identity != identity or state.writer is not writer:
            return True
        if message.kind is WireMessageKind.input_progress:
            state.deadline.advance(
                TypeAdapter(InputTransferProgress).validate_python(message.payload)
            )
        elif message.kind in (WireMessageKind.input_cancelled, WireMessageKind.input_failed):
            payload = (
                TypeAdapter(InputTransferCancelled).validate_python(message.payload)
                if message.kind is WireMessageKind.input_cancelled
                else TypeAdapter(InputTransferFailed).validate_python(message.payload)
            )
            request = state.deadline.request
            if (
                payload.command_id != request.command_id
                or payload.transfer_id != request.slot.transfer_id
            ):
                raise ValueError("input response identity mismatch")
            if message.kind is WireMessageKind.input_cancelled:
                state.cancelled.set()
            elif not state.response.done():
                state.response.set_exception(
                    RemoteInputTransferFailed(cast(InputTransferFailed, payload).message)
                )
        elif message.kind in (WireMessageKind.file_accepted, WireMessageKind.error):
            if state.deadline.active() and not state.response.done():
                if (
                    message.kind is WireMessageKind.file_accepted
                    and state.deadline.phase != "finalization"
                ):
                    state.response.set_exception(
                        RemoteInputTransferFailed("acceptance before finalization")
                    )
                else:
                    state.response.set_result(message)
        else:
            raise ValueError("unexpected input exchange frame")
        return True

    def _revoke_command_input(self, command_id: str) -> None:
        self._revoked_input_commands.add(command_id)
        if command_id in self._possibly_emitted_commands:
            return
        pending = self.pending_commands.pop(command_id, None)
        if pending is not None and not pending[1].done():
            pending[1].cancel()

    def _fail_command_input(
        self, command: ChildCommand, future: asyncio.Future[CommandOutcome], error: Exception
    ) -> None:
        self._revoked_input_commands.add(command.command_id)
        if command.command_id not in self._possibly_emitted_commands:
            self.pending_commands.pop(command.command_id, None)
            if not future.done():
                future.set_exception(RemoteInputTransferFailed(str(error)))

    async def _transfer_command_inputs(self, command: ChildCommand) -> None:
        if self._initial_command_cancelled(command):
            return

        async def transfer() -> None:
            lock = self._input_transfer_locks.setdefault(
                command.child.conversation_id, asyncio.Lock()
            )
            async with lock:
                if not self._initial_command_cancelled(command):
                    await self._transfer_command_inputs_locked(command)

        task = asyncio.create_task(transfer())
        tasks = self._input_tasks.setdefault(command.command_id, set())
        tasks.add(task)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            caller_cancelled = caller is not None and caller.cancelling() > 0
            self._revoke_command_input(command.command_id)
            task.cancel()
            await wait_for_settlement(task)
            if caller_cancelled or not self._initial_command_cancelled(command):
                raise
        finally:
            tasks.discard(task)
            if not tasks:
                self._input_tasks.pop(command.command_id, None)

    async def _transfer_command_inputs_locked(self, command: ChildCommand) -> None:
        identity = self.identity
        if identity is None or identity not in self._transports:
            raise ConnectionError("file generation unavailable")
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
                preparation = asyncio.create_task(
                    asyncio.to_thread(
                        snapshot_file,
                        root=path.parent,
                        relative_path=path.name,
                        scope=FileScope.conversation,
                        owner_id=command.child.conversation_id,
                    )
                )
                try:
                    snapshot = await asyncio.shield(preparation)
                except BaseException:
                    await wait_for_settlement(preparation)
                    if not preparation.cancelled() and preparation.exception() is None:
                        preparation.result().local_path.unlink(missing_ok=True)
                    raise
                slot = None
                try:
                    self.store.verify_connection(identity)
                    slot = await service.create_download_async(snapshot)
                    await self._input_exchange(InputTransferRequest(command.command_id, slot))
                    self.store.verify_connection(identity)
                finally:
                    if slot is not None:
                        service.release(slot.transfer_id)
                    snapshot.local_path.unlink(missing_ok=True)

    async def _emit_command(
        self,
        command: ChildCommand,
        identity: ExecutorIdentity | None = None,
        writer: FrameWriter | None = None,
    ) -> bool:
        identity = self.identity if identity is None else identity
        writer = self.writer if writer is None else writer
        task = asyncio.create_task(self._emit_command_unsettled(command, identity, writer))
        tasks = self._emission_tasks.setdefault(command.command_id, set())
        tasks.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if not self._initial_command_cancelled(command):
                raise
            return False
        finally:
            tasks.discard(task)
            if not tasks:
                self._emission_tasks.pop(command.command_id, None)

    async def _emit_command_unsettled(
        self,
        command: ChildCommand,
        expected_identity: ExecutorIdentity | None = None,
        expected_writer: FrameWriter | None = None,
    ) -> bool:
        """The final guard is shared by immediate sends and reconnect replay."""
        if command.operation not in (
            ChildOperation.answer_permission_ask,
            ChildOperation.answer_user_input,
        ):
            async with self._send_lock:
                if self._initial_command_cancelled(command):
                    return False
                pending = self.pending_commands.get(command.command_id)
                if pending is not None and pending[1].done():
                    return False
                if expected_identity != self.identity or expected_writer is not self.writer:
                    raise ConnectionError("command input generation changed")
                writer = self.writer
                if writer is None or not self.ready:
                    raise ConnectionError("executor link unavailable")
                if not self._command_policy_allows_emission(command):
                    return False
                # From this point even a failed/partial write may have reached native.
                self._possibly_emitted_commands.add(command.command_id)
                if command.operation is ChildOperation.write_prompt:
                    self._prompt_command_ids.add(command.command_id)
                await write_frame(
                    writer,
                    WireMessage(WireMessageKind.command, command.command_id, _object(command)),
                )
                return True
        answer = TypeAdapter(DurableAnswer).validate_python(command.arguments["answer"])
        identity, writer = self.identity, self.writer
        emitted = False

        async def emit() -> None:
            nonlocal emitted
            async with self._send_lock:
                if (
                    identity is None
                    or writer is None
                    or not self.ready
                    or self.identity != identity
                    or self.writer is not writer
                    or command.operation
                    != (
                        ChildOperation.answer_user_input
                        if answer.ask.kind is AskKind.user_input
                        else ChildOperation.answer_permission_ask
                    )
                    or command.child != answer.ask.child
                    or command.turn != answer.ask.turn
                    or command.command_id != "apply_" + answer.answer_id
                    or not self.store.answer_is_deliverable(answer, identity)
                ):
                    return
                await write_frame(
                    writer,
                    WireMessage(WireMessageKind.command, command.command_id, _object(command)),
                )
                emitted = True

        await self.runtime.system.emit_executor_answer(command.child.conversation_id, emit)
        return emitted

    def _release_closed_answer(
        self, command: ChildCommand, future: asyncio.Future[CommandOutcome]
    ) -> None:
        if command.operation in (
            ChildOperation.answer_permission_ask,
            ChildOperation.answer_user_input,
        ):
            answer = TypeAdapter(DurableAnswer).validate_python(command.arguments["answer"])
            if not self.store.answer_target_is_open(answer.ask):
                # No native result exists for an untransmitted closed callback.
                # Cancel bookkeeping only; the durable choice remains unapplied.
                future.cancel()

    def _dispatch_answer(self, answer: DurableAnswer) -> None:
        if (
            not self.ready
            or self.identity is None
            or not self.store.answer_is_deliverable(answer, self.identity)
        ):
            return
        command = ChildCommand(
            "apply_" + answer.answer_id,
            answer.ask.child,
            ChildOperation.answer_user_input
            if answer.ask.kind is AskKind.user_input
            else ChildOperation.answer_permission_ask,
            answer.ask.turn,
            {"answer": _object(answer)},
        )
        if command.command_id in self.pending_commands:
            return
        task = asyncio.create_task(self.send_command(command))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send_new_command(self, command: ChildCommand) -> CommandOutcome:
        """Only RemoteBackendChild._call supplies freshly minted command identities.

        Recovery and reconnect replay use send_command directly. Registration is
        in-process evidence only; a reopened journal never gains unsent provenance.
        """
        if command.operation in (ChildOperation.start, ChildOperation.write_prompt):
            conn = connect(self.db_path)
            try:
                retained = conn.execute(
                    "SELECT runtime_json FROM executor_core_turns WHERE conversation_id=?",
                    (command.child.conversation_id,),
                ).fetchone()
                if retained is not None and any(
                    TypeAdapter(ChildCommand).validate_python(raw) == command
                    for raw in json.loads(retained[0]).get("initial_commands", ())
                ):
                    self._newly_recorded_commands.setdefault(command.command_id, command)
            finally:
                conn.close()
        return await self.send_command(command)

    def _command_policy_allows_emission(self, command: ChildCommand) -> bool:
        """Use original policy facts; never turn uncertain delivery into an unsent refusal."""
        if command.operation not in (ChildOperation.start, ChildOperation.write_prompt):
            return True
        from planner.conversation.admission.contracts import AdmissionExpectation
        from planner.conversation.admission.policy import check_expectation_policy

        conn = connect(self.db_path)
        try:
            if "admission_expectation" not in command.arguments:
                original = conn.execute(
                    "SELECT a.principal_kind,c.backend_key FROM executor_child_assignments a "
                    "LEFT JOIN conversations c ON c.conversation_id=a.conversation_id "
                    "WHERE a.executor_incarnation=? AND a.child_id=? AND a.conversation_id=?",
                    (
                        command.child.executor_incarnation,
                        command.child.child_id,
                        command.child.conversation_id,
                    ),
                ).fetchone()
                scoped_ticket = (
                    original is not None and original[0] == "ticket" and original[1] != "hermes"
                )
                if original is None:
                    retained = conn.execute(
                        "SELECT e.child_json,c.backend_key FROM executor_core_turns e "
                        "JOIN conversations c ON c.conversation_id=e.conversation_id "
                        "JOIN tickets t ON t.conversation_id=e.conversation_id "
                        "WHERE e.conversation_id=?",
                        (command.child.conversation_id,),
                    ).fetchone()
                    scoped_ticket = (
                        retained is not None
                        and retained[1] in ("codex", "claude")
                        and TypeAdapter(ChildReference).validate_json(retained[0]) == command.child
                    )
                if not scoped_ticket:
                    return True
            known = conn.execute(
                "SELECT outcome_json FROM executor_command_outcomes "
                "WHERE executor_incarnation=? AND command_id=?",
                (command.child.executor_incarnation, command.command_id),
            ).fetchone()
            pending = self.pending_commands.get(command.command_id)
            if known is not None:
                outcome = TypeAdapter(CommandOutcome).validate_json(known[0])
                if outcome.child != command.child:
                    raise ValueError("recorded outcome conflicts with command child")
                if pending is not None and not pending[1].done():
                    pending[1].set_result(outcome)
                return False
            try:
                expected = TypeAdapter(AdmissionExpectation).validate_python(
                    command.arguments.get("admission_expectation", {})
                )
                check_expectation_policy(conn, expected)
                if expected.executor_identity != self.identity:
                    raise AdmissionRefused(
                        "The destination connection changed before submission",
                        reason="context_changed",
                        corrective_action="verify_launch_context",
                    )
            except (AdmissionRefused, ValueError, TypeError) as failure:
                error = (
                    failure
                    if isinstance(failure, AdmissionRefused)
                    else AdmissionRefused(
                        "recorded launch expectation is unproved",
                        reason="identity_unproved",
                        corrective_action="verify_launch_context",
                    )
                )
                if (
                    self._newly_recorded_commands.get(command.command_id) != command
                    or command.command_id in self._possibly_emitted_commands
                ):
                    if command.command_id not in self._policy_held_commands:
                        logging.getLogger("planner.executor").warning(
                            "Command %s on %s remains uncertain; policy blocks redispatch: %s. "
                            "Original evidence or definitive executor settlement is required.",
                            command.command_id,
                            command.child,
                            error,
                        )
                        self._policy_held_commands.add(command.command_id)
                    return False
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.refused,
                    {
                        "error_type": "AdmissionRefused",
                        "error_message": str(error),
                        "reason": error.reason,
                        "corrective_action": error.corrective_action,
                    },
                )
                # This is a parent pre-transport refusal, not a native FIFO receipt.
                # The existing canonical outcome transaction settles its exact lease.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    self.store.record_command_outcome_in_transaction(conn, outcome)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                if pending is not None and not pending[1].done():
                    pending[1].set_result(outcome)
                self._newly_recorded_commands.pop(command.command_id, None)
                return False
            return True
        finally:
            conn.close()

    async def send_command(self, command: ChildCommand) -> CommandOutcome:
        if self._initial_command_cancelled(command):
            raise RemoteInitialDeliveryCancelled()
        existing = self.pending_commands.get(command.command_id)
        if existing is not None:
            if existing[0] != command:
                raise ValueError("command identity reused with different content")
            return await asyncio.shield(existing[1])
        future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        self.pending_commands[command.command_id] = (command, future)
        try:
            if self.ready and self.writer is not None:
                identity, writer = self.identity, self.writer
                try:
                    try:
                        await self._transfer_command_inputs(command)
                    except (ConnectionError, TimeoutError):
                        raise
                    except (RemoteInputTransferFailed, ValueError, OSError) as error:
                        self._fail_command_input(command, future, error)
                    else:
                        if not future.done() and not await self._emit_command(
                            command, identity, writer
                        ):
                            self._release_closed_answer(command, future)
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

    def _closed_reconciliation_child(
        self, child: ChildReference, identity: ExecutorIdentity
    ) -> bool:
        # Historical assignment is evidence for settling original outcomes only.
        # It never makes this child adoptable or restores its work authority.
        if child.executor_incarnation != identity.executor_incarnation:
            raise ValueError("reconciliation child incarnation conflicts with connection")
        if self.store.has_closed_child_assignment(child):
            return True
        self._assignment(child)
        return False

    def _require_application_assignment(
        self, application: AnswerApplication, identity: ExecutorIdentity, conversation_id: str
    ) -> None:
        child = application.ask.child
        if (
            child.executor_incarnation != identity.executor_incarnation
            or child.conversation_id != conversation_id
            or (
                self.store.child_assignment(child) is None
                and not self.store.has_closed_child_assignment(child)
            )
        ):
            raise ValueError("application lacks the original authenticated child assignment")

    async def accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        lock = self._conversation_locks.setdefault(envelope.conversation_id, asyncio.Lock())
        async with lock:
            receipt = await self._accept_envelope(
                envelope,
                identity,
            )
            return receipt

    async def _accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        previous = self.store.preflight(
            envelope,
            identity=identity,
        )
        if previous is not None:
            if envelope.kind is EnvelopeKind.command_outcome:
                self._resolve_command(TypeAdapter(CommandOutcome).validate_python(envelope.payload))
            return previous
        if envelope.kind is EnvelopeKind.backend_event:
            event = TypeAdapter(BackendEvent).validate_python(envelope.payload)
            self._assignment(event.child)
            if event.child.conversation_id != envelope.conversation_id:
                raise ValueError("event conversation conflicts with envelope")

            def accept(row: QueuedEnvelope, write: Any, **publication: Any) -> AcceptanceReceipt:
                def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    conn = connection
                    accepted = envelope
                    result = write(conn, accepted)
                    if (
                        event.method is BackendEventMethod.permission_ask_raised
                        and event.turn is not None
                        and result.get("applied") is True
                    ):
                        from planner.executor.codec import decode_event_arguments

                        decoded = decode_event_arguments(event)
                        from planner.conversation.backends.contracts import (
                            PermissionAskRaisedArguments,
                        )

                        assert isinstance(decoded, PermissionAskRaisedArguments)
                        ask = decoded.ask
                        self.store.publish_ask_in_transaction(
                            conn,
                            AskReference(event.child, event.turn, ask.ask_id),
                            tuple(option.option_id for option in ask.options),
                            now_ms(),
                        )
                    if (
                        event.method is BackendEventMethod.user_input_requested
                        and event.turn is not None
                        and result.get("applied") is True
                    ):
                        from planner.executor.codec import decode_event_arguments

                        decoded = decode_event_arguments(event)
                        from planner.conversation.backends.contracts import (
                            UserInputRequestedArguments,
                        )

                        assert isinstance(decoded, UserInputRequestedArguments)
                        request = decoded.request
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

                return self.store.accept(
                    row,
                    effect,
                    now_ms(),
                    identity=identity,
                    **publication,
                )

            return cast(
                AcceptanceReceipt,
                await self.runtime.system.receive_remote_backend_event(
                    event,
                    envelope,
                    accept,
                ),
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
            preparation_error: PlannerError | None = None
            creation_validation: CanonicalModelValidation | None = None
            if write.operation == "ticket.create":
                from planner.runtime.conversation_start import refuse_known_incompatibility
                from planner.tickets.api import (
                    _marshal_create_ticket,
                    validate_ticket_creation,
                    warm_ticket_creation_model,
                )

                connection = connect(self.db_path)
                try:
                    body = write.arguments.get("body")
                    if not isinstance(body, dict):
                        raise PlannerError(ErrorCode.validation, "ticket mutation needs a body", {})
                    parsed = _marshal_create_ticket(body)
                    resolver = getattr(self.runtime, "model_resolution", None)
                    if resolver is None:
                        raise PlannerError(
                            ErrorCode.gateway_offline, "the model resolver is unavailable", {}
                        )
                    await warm_ticket_creation_model(
                        connection,
                        resolver,
                        worker_type=parsed["worker_type"],
                        employee_backend=parsed.get("employee_backend"),
                        employee_launch_model=parsed.get("employee_launch_model"),
                        employee_launch_reasoning_effort=parsed.get(
                            "employee_launch_reasoning_effort"
                        ),
                    )
                    creation_validation = await validate_ticket_creation(
                        body,
                        connection,
                        self.runtime.system,
                        principal=principal,
                    )
                    refuse_known_incompatibility(creation_validation)
                except PlannerError as error:
                    if error.code == ErrorCode.gateway_offline:
                        raise
                    preparation_error = error
                finally:
                    connection.close()
            from planner.tickets.contracts import ReviewTransferNotice
            from planner.tickets.review_transfer import notify_review_transfers

            notices: list[ReviewTransferNotice] = []

            def mutate(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                # This read shares the receipt's BEGIN IMMEDIATE and domain write.
                # A separate post-probe read cannot fence closure committed while
                # the receipt transaction waits for its SQLite write lock.
                current_assignment = connection.execute(
                    "SELECT principal_kind,principal_id FROM executor_child_assignments "
                    "WHERE executor_incarnation=? AND child_id=? AND conversation_id=? "
                    "AND closed_at_ms IS NULL",
                    (
                        write.child.executor_incarnation,
                        write.child.child_id,
                        write.child.conversation_id,
                    ),
                ).fetchone()
                if current_assignment is None or tuple(current_assignment) != (
                    principal.kind.value,
                    principal.id,
                ):
                    raise PlannerError(ErrorCode.agent_forbidden, "child assignment changed", {})
                if preparation_error is not None:
                    raise preparation_error
                return write_ticket_mutation(
                    connection,
                    write,
                    principal,
                    self.clock,
                    review_transfer_notices=notices,
                    creation_validation=creation_validation,
                )

            receipt = self.store.accept(
                envelope,
                mutate,
                now_ms(),
                identity=identity,
            )
            if notices:
                connection = connect(self.db_path)
                try:
                    await notify_review_transfers(
                        self.runtime.system, connection, notices, now=self.clock.now_unix()
                    )
                finally:
                    connection.close()
            return receipt

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
            answer = self.store.answer_by_id(outcome.command_id.removeprefix("apply_"))
            historical_answer_outcome = (
                answer is not None
                and answer.ask.child == outcome.child
                and outcome.child.executor_incarnation == identity.executor_incarnation
                and self.store.has_closed_child_assignment(outcome.child)
            )
            historical_initial_outcome = (
                outcome.child.executor_incarnation == identity.executor_incarnation
                and self.store.has_closed_child_assignment(outcome.child)
                and self.store.has_retained_command(outcome)
            )
            if not (retired_refusal or historical_answer_outcome or historical_initial_outcome):
                self._assignment(outcome.child)
            application_payload = outcome.result.get("answer_application")
            if not retired_refusal and isinstance(application_payload, dict):
                application = TypeAdapter(AnswerApplication).validate_python(application_payload)
                if application.ask.child != outcome.child or outcome.command_id not in (
                    application.answer_id,
                    "apply_" + application.answer_id,
                ):
                    raise ValueError("answer application conflicts with command child or identity")
                self._require_application_assignment(
                    application, identity, envelope.conversation_id
                )

                def record(connection: Any, applied: AnswerApplication) -> None:
                    self.store.record_application_in_transaction(connection, applied)
                    self.store.record_command_outcome_in_transaction(connection, outcome)

                receipt = await self.runtime.system.receive_remote_answer_application(
                    application,
                    envelope,
                    lambda row, write, **publication: self.store.accept(
                        row,
                        write,
                        now_ms(),
                        identity=identity,
                        **publication,
                    ),
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
            self._require_application_assignment(application, identity, envelope.conversation_id)
            return cast(
                AcceptanceReceipt,
                await self.runtime.system.receive_remote_answer_application(
                    application,
                    envelope,
                    lambda row, write, **publication: self.store.accept(
                        row,
                        write,
                        now_ms(),
                        identity=identity,
                        **publication,
                    ),
                    self.store.record_application_in_transaction,
                ),
            )
        raise ValueError("unknown executor envelope")

    def _command_outcome(self, conn: Any, outcome: CommandOutcome) -> JsonObject:
        self.store.record_command_outcome_in_transaction(conn, outcome)
        return {"recorded": True}

    def _application(self, conn: Any, application: AnswerApplication) -> JsonObject:
        self.store.record_application_in_transaction(conn, application)
        return {"recorded": True}


    def _resolve_command(self, outcome: CommandOutcome) -> None:
        if not self._outcome_hint_acknowledged(outcome):
            return
        if outcome.disposition is not CommandDisposition.uncertain:
            self._newly_recorded_commands.pop(outcome.command_id, None)
            self._policy_held_commands.discard(outcome.command_id)
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
        context = self.store.turn_context(conversation_id)
        return (context is not None and not context.source_retired) or any(
            child.reference.conversation_id == conversation_id for child in self.children.values()
        )

    def conversation_asks(self, conversation_id: str) -> list[JsonObject]:
        result: list[JsonObject] = []
        for evidence in self.store.evidence_for_conversation(conversation_id):
            answer = self.store.recorded_answer(evidence.ask)
            result.append(
                {
                    "evidence": _object(evidence),
                    "answer": None if answer is None else _object(answer),
                }
            )
        return result

    def conversation_context(self, conversation_id: str) -> JsonObject | None:
        context = self.store.turn_context(conversation_id)
        if context is None or context.source_retired:
            return None
        return {"child": _object(context.child), "turn": _object(context.turn)}

    def _current_ask_evidence(
        self, conversation_id: str, ask_id: str, kind: AskKind = AskKind.permission
    ) -> Any:
        context = self.store.turn_context(conversation_id)
        if context is None:
            return None
        for evidence in reversed(self.store.evidence_for_conversation(conversation_id)):
            if (
                self.store.public_request_id(evidence.ask) == ask_id
                and evidence.ask.child == context.child
                and evidence.ask.turn == context.turn
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
            "answer_available": evidence.answer_id is None
            and self.store.answer_target_is_open(evidence.ask),
        }

    async def answer_permission(
        self,
        conversation_id: str,
        ask_id: str,
        option_id: str,
        executor_ask: AskReference | None = None,
    ) -> bool:
        if (
            executor_ask is None
            or executor_ask.child.conversation_id != conversation_id
            or self.store.public_request_id(executor_ask) != ask_id
            or executor_ask.kind is not AskKind.permission
        ):
            return False
        previous = self.store.recorded_answer(executor_ask)
        try:
            answer = self.store.choose_answer(executor_ask, option_id, 0, now_ms())
        except PlannerError:
            return False
        if previous is None:
            self._dispatch_answer(answer)
        return True

    async def answer_user_input(
        self,
        conversation_id: str,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
        executor_ask: AskReference | None = None,
    ) -> bool:
        if (
            executor_ask is None
            or executor_ask.child.conversation_id != conversation_id
            or self.store.public_request_id(executor_ask) != request_id
            or executor_ask.kind is not AskKind.user_input
        ):
            return False
        previous = self.store.recorded_answer(executor_ask)
        try:
            answer = self.store.choose_user_input_answer(executor_ask, answers, 0, now_ms())
        except PlannerError:
            return False
        if previous is None:
            self._dispatch_answer(answer)
        return True

    async def _session(self, reader: FrameReader, writer: FrameWriter) -> None:
        identity = None
        forwards: PreviewForwards | None = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                hello = await read_frame(reader)
            if hello.kind is not WireMessageKind.hello:
                raise ValueError("executor handshake required")
            parsed_identity = parse_identity(hello.payload.get("identity"))
            health = TypeAdapter(ExecutorHealth).validate_python(hello.payload)
            if health.identity != parsed_identity:
                raise ValueError("identity coercion refused")
            if type(hello.payload.get("process_id")) is not int or health.process_id < 1:
                raise ValueError("invalid executor process identity")
            old_identity = self.identity
            old_writer = self.writer
            self.store.begin_connection(health.identity, now_ms())
            # Only an admitted writer owns generation cleanup. A duplicate hello
            # must not revoke resources from the already accepted connection.
            identity = health.identity
            self.process_id = health.process_id
            self.observed_at_ms = now_ms()
            self.lost_at_ms = None
            old_previews = self._preview_forwards
            self._fail_catalogs(old_writer)
            self.clock_sample = None
            self.identity, self.writer, self.ready = identity, writer, False
            self.last_contact_at_ms = now_ms()

            self._preview_forwards = None
            if old_writer is not None and old_writer is not writer:
                old_writer.close()
            try:
                if old_previews is not None:
                    await old_previews.close()
            finally:
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
                task = asyncio.create_task(
                    self._handle(
                        message,
                        identity,
                        writer,
                    )
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except (
            asyncio.IncompleteReadError,
            ConnectionError,
            OSError,
            ValueError,
            TimeoutError,
            PlannerError,
        ):
            pass
        except BaseException:
            raise
        finally:
            if identity is not None and self.writer is writer and self.identity == identity:
                # Detach this generation before cleanup yields to a replacement hello.
                self.ready = False
                forwards = self._preview_forwards
                self._preview_forwards = None
                self.writer = None
                self.lost_at_ms = now_ms()
                self.store.mark_lost(identity, now_ms())
                self._fail_catalogs(writer)
                for exchange in self._input_exchanges.values():
                    if exchange.writer is writer and not exchange.response.done():
                        exchange.response.set_exception(ConnectionError("executor link lost"))
                for response_future in self._responses.values():
                    if not response_future.done():
                        response_future.set_exception(ConnectionError("executor link lost"))
            writer.close()
            current_forwards = self._preview_forwards if self.identity == identity else None
            try:
                if forwards is not None:
                    await forwards.close()
            finally:
                try:
                    if current_forwards is not None:
                        await current_forwards.close()
                finally:
                    if identity is not None:
                        await self._close_transport(identity)

    async def _measure_clock(self) -> None:
        identity, writer = self.identity, self.writer
        sent = now_ms()
        response = await self._request(
            WireMessageKind.health, {"clock_probe": {"vps_sent_at_ms": sent}}
        )
        if self.identity != identity or self.writer is not writer:
            return
        self.clock_sample = ClockSample(
            sent,
            int(cast(int, response.payload["mac_received_at_ms"])),
            int(cast(int, response.payload["mac_sent_at_ms"])),
            now_ms(),
        )

    async def _handle(
        self,
        message: WireMessage,
        identity: ExecutorIdentity,
        writer: FrameWriter,
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
        writer: FrameWriter,
    ) -> None:
        if self.writer is not writer or self.identity != identity:
            return
        try:
            if self._receive_input_frame(message, identity, writer):
                return
            catalog = self._catalog_responses.get(message.request_id)
            if catalog is not None and catalog[1] is writer:
                if not catalog[2].done():
                    catalog[2].set_result(message)
                return
            if message.kind is WireMessageKind.catalog_response:
                return  # Revoked, expired, or superseded observations stay revoked.
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
                    root = _generation_root()
                    root_path = Path(root.name)
                    service = GenerationTransfers(
                        identity,
                        root_path / "s",
                        lambda: self.identity == identity and self.writer is writer,
                    )
                    try:
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
                        preview_request,
                        lambda: self.identity == identity and self.writer is writer and self.ready,
                    )
                    result, response_kind = (
                        {},
                        WireMessageKind.transport_opened,
                    )
            elif message.kind is WireMessageKind.reconcile_inventory:
                inventory = TypeAdapter(ReconciliationInventory).validate_python(message.payload)
                if inventory.identity != identity:
                    raise ValueError("stale reconciliation generation")
                self.store.verify_connection(identity)
                for item in inventory.children:
                    if self._closed_reconciliation_child(item.child, identity):
                        self.retire_child(item.child)
                        continue
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
                        proxy = self.recover_child(item.child)
                        self.children[item.child.child_id] = proxy
                    await self.runtime.system.adopt_remote_child(item, proxy)
                    if not self._reconciliation_current(identity, writer):
                        return
                    # Adoption can wait behind shutdown. Its earlier assignment
                    # precheck grants no authority after that await.
                    if self._closed_reconciliation_child(item.child, identity):
                        self.retire_child(item.child)
                for outcome in inventory.retained_command_outcomes:
                    previous_outcome = self._retained_inventory.get(outcome.command_id)
                    if previous_outcome is not None and previous_outcome != outcome:
                        raise ValueError(
                            "conflicting retained command outcome across inventory chunks"
                        )
                    historical = outcome.child.executor_incarnation != identity.executor_incarnation
                    if historical:
                        if not self.store.has_retained_command(outcome):
                            raise ValueError(
                                "historical outcome lacks exact retained command metadata"
                            )
                        if not self.store.has_accepted_command_outcome(outcome):
                            raise ValueError(
                                "historical outcome lacks matching accepted canonical receipt"
                            )
                    else:
                        self._closed_reconciliation_child(outcome.child, identity)
                    if not self._reconciliation_current(identity, writer):
                        return
                    self._retained_inventory[outcome.command_id] = outcome
                    if not historical:
                        self._resolve_command(outcome)
                if not inventory.children and not inventory.retained_command_outcomes:
                    if not self._reconciliation_current(identity, writer):
                        return
                    merged = replace(
                        inventory,
                        children=tuple(
                            item
                            for item in self._inventory.values()
                            if not self._closed_reconciliation_child(item.child, identity)
                        ),
                        retained_command_outcomes=tuple(self._retained_inventory.values()),
                    )
                    reconciliation = self.store.reconcile(merged, now_ms())
                    # Detach synchronously: a replacement handler may populate
                    # its own aggregates while any of the ACK writes awaits.
                    retained_outcomes = self._retained_inventory
                    self._inventory = {}
                    self._retained_inventory = {}
                    for command_id in reconciliation.acknowledged_command_ids:
                        retained_outcome = retained_outcomes.get(command_id)
                        if retained_outcome is not None and not self._outcome_hint_acknowledged(
                            retained_outcome
                        ):
                            continue
                        if not await self._send_reconciliation(
                            WireMessage(
                                WireMessageKind.command_outcome_ack,
                                command_id,
                                {"command_id": command_id},
                            ),
                            identity,
                            writer,
                        ):
                            return
                    result = _object(
                        ReconciliationResult(identity, reconciliation.receipts, (), (), ())
                    )
                else:
                    result = _object(ReconciliationResult(identity, (), (), (), ()))
                response_kind = WireMessageKind.reconcile_result
            elif message.kind is WireMessageKind.reconciliation_ready:
                # FIFO puts the ready ACK before any newly admitted preview or
                # command. The Mac rejects previews until it receives this ACK.
                async with self._send_lock:
                    if self.writer is not writer or self.identity != identity:
                        return
                    await write_frame(
                        writer,
                        WireMessage(
                            WireMessageKind.reconciliation_ready,
                            message.request_id,
                            {"ready": True},
                        ),
                    )
                    if self.writer is not writer or self.identity != identity:
                        return
                    self.ready = True
                if hasattr(self.store, "mark_ready"):
                    self.store.mark_ready(identity, now_ms())
                await self.runtime.system.resume_executor_held_prompts(remote_ready=True)
                if self.writer is not writer or self.identity != identity:
                    return
                for answer in self.store.unapplied_answers():
                    self._dispatch_answer(answer)
                # Resending a stable command to the same live registry is safe:
                # the registry retains its pending task and first outcome.
                for command, future in tuple(self.pending_commands.values()):
                    if (
                        not future.done()
                        and command.child.executor_incarnation == identity.executor_incarnation
                    ):
                        try:
                            await self._transfer_command_inputs(command)
                        except (ConnectionError, TimeoutError):
                            raise
                        except (RemoteInputTransferFailed, ValueError, OSError) as error:
                            self._fail_command_input(command, future, error)
                            continue
                        if not future.done() and not await self._emit_command(
                            command, identity, writer
                        ):
                            self._release_closed_answer(command, future)
                return
            elif message.kind is WireMessageKind.envelope:
                envelope = TypeAdapter(QueuedEnvelope).validate_python(message.payload)
                result, response_kind = (
                    _object(
                        await self.accept_envelope(
                            envelope,
                            identity,
                        )
                    ),
                    WireMessageKind.receipt,
                )
            elif message.kind is WireMessageKind.command_outcome:
                outcome = TypeAdapter(CommandOutcome).validate_python(message.payload)
                self._resolve_command(outcome)
                if not self._outcome_hint_acknowledged(outcome):
                    return
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
                try:
                    response = WireMessage(response_kind, message.request_id, result)
                    if message.kind is WireMessageKind.reconcile_inventory:
                        if not await self._send_reconciliation(response, identity, writer):
                            return
                    else:
                        await self._send(response)
                except BaseException:
                    raise
                else:
                    pass
        except (sqlite3.OperationalError, OSError):
            writer.close()
        except (ValueError, PlannerError, KeyError, TypeError) as error:
            if self.writer is writer:
                response = WireMessage(
                    WireMessageKind.error,
                    message.request_id,
                    error.to_payload()
                    if isinstance(error, PlannerError)
                    else {"error": {"code": "validation", "message": str(error)}},
                )
                if message.kind is WireMessageKind.reconcile_inventory:
                    await self._send_reconciliation(response, identity, writer)
                else:
                    await self._send(response)

    async def _read(self, read: LiveReadRequest) -> JsonObject:
        self._assignment(read.child)
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

        principal = self._assignment(read.child)
        headers = {"X-Plan-Actor": principal_legacy_actor(principal)}
        if principal.kind is PrincipalKind.ticket:
            headers["X-Plan-Ticket-ID"] = principal.id
        elif principal.kind is PrincipalKind.manager:
            headers["X-Plan-Manager-ID"] = principal.id
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
            raise ValueError("file generation unavailable")
        publication = TypeAdapter(FilePublicationRequest).validate_python(message.payload)
        self._authorize_file(publication)
        manifest = publication.manifest
        key = identity, manifest.publication_id
        if message.kind is WireMessageKind.file_manifest:
            assigned = self._file_assignments.get(manifest.publication_id)
            if assigned is not None and assigned != publication.child:
                raise ValueError("file publication assignment changed")
            self._file_assignments[manifest.publication_id] = publication.child
            await self._receive_work(
                identity, self.file_receiver.begin, manifest, identity=identity
            )
            self.store.verify_connection(identity)
            if self.store.publication_accepted(manifest):
                return _object(manifest), WireMessageKind.file_accepted
            previous = self._upload_transfers.pop(key, None)
            if previous is not None:
                service.release(previous)
            slot = service.create_upload(manifest)
            self._upload_transfers[key] = slot.transfer_id
            return _object(slot), WireMessageKind.file_slot
        if self._file_assignments.get(manifest.publication_id) != publication.child:
            raise ValueError("file publication assignment changed")
        transfer_id = self._upload_transfers.pop(key, None)
        if transfer_id is None:
            if self.store.publication_accepted(manifest):
                return _object(manifest), WireMessageKind.file_accepted
            raise ValueError("unknown publication transfer")
        try:
            staged = service.upload_path(transfer_id)
            self.store.verify_connection(identity)
            self._authorize_file(publication)
            await self._receive_work(
                identity, self.file_receiver.ingest_staged, manifest, staged, identity=identity
            )
            self.store.verify_connection(identity)
            self._authorize_file(publication)
            await self._receive_work(
                identity, self.file_receiver.complete, manifest, now_ms(), identity=identity
            )
            self.store.verify_connection(identity)
            return _object(manifest), WireMessageKind.file_accepted
        finally:
            service.release(transfer_id)

    async def _receive_work(
        self, generation: ExecutorIdentity, operation: Any, *args: Any, **kwargs: Any
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
