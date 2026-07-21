"""Employee-keyed ACP child ownership and durable session binding."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from acp.schema import (
    DeniedOutcome,
    ForkSessionRequest,
    LoadSessionRequest,
    NewSessionRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
)

from .backend_catalog import (
    EmployeeBackendCatalog,
    MaterializedEmployeeBackendRegistration,
)
from .backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AgentBackendDefinition,
    PermissionRequestCallback,
)
from .configuration import (
    ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
    ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS,
)
from .contracts import (
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationSessionBinding,
)
from .runtime_ports import (
    CompactionCaptureTransition,
    ConversationCompactionCaptureDeadlineExpired,
    ConversationCompactionCaptureFailed,
    ConversationCompactionCapturePhase,
    ConversationCompactionDurableBindingDisposition,
    ConversationRuntimeGenerationFatal,
    ConversationRuntimeHandle,
    ConversationRuntimeLease,
    ConversationRuntimeUnavailable,
    ConversationWaitDeadlineExpired,
    GenerationBoundBackendTurnStrategy,
    PreparedCompactionCapture,
    RequestedCancelRuntimeReplacement,
)
from .sdk_child import build_panels_initialize_request
from .wire_contracts import ProtocolUpdateRejectedPayload

ResolveConversationBinding = Callable[[str], Awaitable[ConversationSessionBinding | None]]
CompareAndSwapConversationBinding = Callable[
    [ConversationSessionBinding | None, ConversationSessionBinding],
    Awaitable[ConversationSessionBinding],
]
ResolveConversationCompactionBoundaries = Callable[
    [ConversationSessionBinding],
    Awaitable[tuple[ConversationCompactionBoundaryProvenance, ...]],
]
CompareAndSwapConversationCompaction = Callable[
    [
        ConversationSessionBinding,
        tuple[ConversationCompactionBoundaryProvenance, ...],
        ConversationSessionBinding,
        tuple[ConversationCompactionBoundaryProvenance, ...],
    ],
    Awaitable[ConversationSessionBinding],
]
ConversationChildDeathCallback = Callable[
    [ConversationEmployee, int, BaseException | None], Awaitable[None]
]
SourceAwarePermissionRequestCallback = Callable[
    [ConversationRuntimeHandle, RequestPermissionRequest],
    Awaitable[RequestPermissionResponse],
]


@dataclass(frozen=True, slots=True)
class ConversationIngressSource:
    employee: ConversationEmployee
    child_generation: int
    record_identity: object


SourceAwareConversationIngress = Callable[[ConversationIngressSource, object], Awaitable[None]]
SourceAwareChildDeathCallback = Callable[
    [ConversationIngressSource, BaseException | None], Awaitable[None]
]


class AcpEmployeeRegistryError(RuntimeError):
    pass


class AcpEmployeeRegistryClosed(AcpEmployeeRegistryError):
    pass


class AcpEmployeeBindingError(AcpEmployeeRegistryError):
    pass


class AcpEmployeeStaleGeneration(AcpEmployeeRegistryError):
    pass


class AcpEmployeeRegistryShutdownError(AcpEmployeeRegistryError):
    def __init__(self, unfinished_employee_ids: tuple[str, ...]) -> None:
        super().__init__(
            "ACP employee registry shutdown exceeded its deadline: "
            + ", ".join(unfinished_employee_ids)
        )
        self.unfinished_employee_ids = unfinished_employee_ids


@dataclass(frozen=True, slots=True)
class AcpEmployeeRecord:
    employee: ConversationEmployee
    binding: ConversationSessionBinding
    child_generation: int
    child: AcpEmployeeChild
    record_identity: object


@dataclass(slots=True)
class _PlannedRetirement:
    transaction_id: str
    record_identity: object
    settlement: asyncio.Future[None]


@dataclass(slots=True)
class _PreparedCompactionCaptureState:
    prepared: PreparedCompactionCapture
    capture_transaction_id: str
    lifecycle_mutation_gate: asyncio.Lock
    candidate_generation: int
    candidate_child: AcpEmployeeChild
    candidate_record_identity: object
    settling: bool = False


_Awaited = TypeVar("_Awaited")


class AcpEmployeeRegistry:
    def __init__(
        self,
        *,
        backend_catalog: EmployeeBackendCatalog,
        materialized_backends: tuple[MaterializedEmployeeBackendRegistration, ...],
        resolve_binding: ResolveConversationBinding,
        compare_and_swap_binding: CompareAndSwapConversationBinding,
        conversation_ingress: AcpConversationIngress,
        permission_callback: PermissionRequestCallback,
        conversation_child_death_callback: ConversationChildDeathCallback | None = None,
        source_aware_permission_callback: SourceAwarePermissionRequestCallback | None = None,
        source_aware_conversation_ingress: SourceAwareConversationIngress | None = None,
        source_aware_child_death_callback: SourceAwareChildDeathCallback | None = None,
        resolve_compaction_boundaries: ResolveConversationCompactionBoundaries | None = None,
        compare_and_swap_compaction: CompareAndSwapConversationCompaction | None = None,
    ) -> None:
        materialized_keys = tuple(
            backend.definition.backend_key for backend in materialized_backends
        )
        if materialized_keys != backend_catalog.registered_backend_keys():
            raise ValueError("materialized employee backends must exactly match catalog order")
        self._backend_catalog = backend_catalog
        self._definitions = {
            backend.definition.backend_key: backend.definition for backend in materialized_backends
        }
        self._factories = {
            backend.definition.backend_key: backend.child_factory
            for backend in materialized_backends
        }
        self._resolve_binding = resolve_binding
        self._compare_and_swap_binding = compare_and_swap_binding
        self._conversation_ingress = conversation_ingress
        self._permission_callback = permission_callback
        self._conversation_child_death_callback = conversation_child_death_callback
        self._source_aware_permission_callback = source_aware_permission_callback
        self._source_aware_conversation_ingress = source_aware_conversation_ingress
        self._source_aware_child_death_callback = source_aware_child_death_callback
        self._resolve_compaction_boundaries = resolve_compaction_boundaries
        self._compare_and_swap_compaction = compare_and_swap_compaction
        self._lock = asyncio.Lock()
        self._lifecycle_mutation_gates: dict[str, asyncio.Lock] = {}
        self._publication_update_gates: dict[str, asyncio.Lock] = {}
        self._records: dict[str, AcpEmployeeRecord] = {}
        self._initialization_tasks: dict[str, asyncio.Task[AcpEmployeeRecord]] = {}
        self._attach_tasks: dict[str, asyncio.Task[AcpEmployeeRecord]] = {}
        self._new_conversation_tasks: dict[str, asyncio.Task[AcpEmployeeRecord]] = {}
        self._next_generation_by_employee: dict[str, int] = {}
        self._accepted_callback_generations: dict[str, set[int]] = {}
        self._record_identity_by_generation: dict[tuple[str, int], object] = {}
        self._planned_retirements: dict[tuple[str, int, int], _PlannedRetirement] = {}
        self._reserved_callback_generations: set[tuple[str, int]] = set()
        self._prepared_compaction_captures: dict[object, _PreparedCompactionCaptureState] = {}
        self._closing = False

    async def get_or_spawn(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        async with self._lock:
            self._require_open_locked()
            self._definition_for(employee.backend_key)
            current = self._records.get(employee.employee_id)
            if (
                current is not None
                and current.child.alive
                and current.employee == employee
                and current.binding.backend_key == employee.backend_key
            ):
                return current
            existing_task = self._initialization_tasks.get(employee.employee_id)
            if existing_task is None:
                generation = self._allocate_generation_locked(employee.employee_id)
                existing_task = asyncio.create_task(
                    self._initialize_record(employee, generation),
                    name=f"panels.acp.initialize.{employee.employee_id}.{generation}",
                )
                self._initialization_tasks[employee.employee_id] = existing_task
        try:
            record = await asyncio.shield(existing_task)
        finally:
            if existing_task.done():
                async with self._lock:
                    if self._initialization_tasks.get(employee.employee_id) is existing_task:
                        self._initialization_tasks.pop(employee.employee_id, None)
        if not self._record_matches(record, employee):
            return await self.get_or_spawn(employee)
        return record

    async def attach(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        async with self._lock:
            self._require_open_locked()
            task = self._attach_tasks.get(employee.employee_id)
            if task is None:
                had_live_record = (
                    (record := self._records.get(employee.employee_id)) is not None
                    and record.child.alive
                    and record.employee == employee
                )
                task = asyncio.create_task(
                    self._attach_wave(employee, had_live_record),
                    name=f"panels.acp.attach.{employee.employee_id}",
                )
                self._attach_tasks[employee.employee_id] = task
        try:
            record = await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._attach_tasks.get(employee.employee_id) is task:
                        self._attach_tasks.pop(employee.employee_id, None)
        if not self._record_matches(record, employee):
            return await self.attach(employee)
        return record

    async def _attach_wave(
        self, employee: ConversationEmployee, had_live_record: bool
    ) -> AcpEmployeeRecord:
        record = await self.get_or_spawn(employee)
        if not had_live_record:
            return record
        captured: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

        async def private_ingress(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            captured.append(item)

        lifecycle_gate = await self._lifecycle_mutation_gate(employee.employee_id)
        publication_gate = await self._publication_update_gate(employee.employee_id)
        async with lifecycle_gate:
            async with publication_gate:
                async with self._lock:
                    current = self._records.get(employee.employee_id)
                    if current is not record or not record.child.alive:
                        raise AcpEmployeeStaleGeneration(
                            "attach started on a stale child generation"
                        )
            request = self._load_request(record.employee, record.binding.acp_session_id)
            await record.child.capture_load_session(
                request, cast(AcpConversationIngress, private_ingress)
            )
            async with publication_gate:
                async with self._lock:
                    current = self._records.get(employee.employee_id)
                    if current is not record or not record.child.alive:
                        raise AcpEmployeeStaleGeneration(
                            "attach completed on a stale child generation"
                        )
        await self._publish_captured_replay(record, tuple(captured))
        return record

    async def _publish_captured_replay(
        self,
        record: AcpEmployeeRecord,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
    ) -> None:
        source = ConversationIngressSource(
            employee=record.employee,
            child_generation=record.child_generation,
            record_identity=record.record_identity,
        )
        for item in replay:
            async with self._lock:
                current = self._records.get(record.employee.employee_id)
                if current is not record or not record.child.alive:
                    raise AcpEmployeeStaleGeneration(
                        "attach replay belongs to a stale child generation"
                    )
            if self._source_aware_conversation_ingress is not None:
                await self._source_aware_conversation_ingress(source, item)
            else:
                await self._conversation_ingress(item)

    async def new_conversation(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        async with self._lock:
            self._require_open_locked()
            task = self._new_conversation_tasks.get(employee.employee_id)
            if task is None:
                task = asyncio.create_task(
                    self._replace_conversation(employee),
                    name=f"panels.acp.new-conversation.{employee.employee_id}",
                )
                self._new_conversation_tasks[employee.employee_id] = task
        try:
            record = await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._new_conversation_tasks.get(employee.employee_id) is task:
                        self._new_conversation_tasks.pop(employee.employee_id, None)
        if not self._record_matches(record, employee):
            return await self.new_conversation(employee)
        return record

    async def _replace_conversation(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        record = await self.get_or_spawn(employee)
        original_handle = self._runtime_handle(record)
        old_binding = record.binding
        session_created = False
        child_to_close: AcpEmployeeChild | None = None
        lifecycle_gate = await self._lifecycle_mutation_gate(employee.employee_id)
        publication_gate = await self._publication_update_gate(employee.employee_id)
        await lifecycle_gate.acquire()
        try:
            async with publication_gate:
                async with self._lock:
                    current = self._records.get(employee.employee_id)
                    if not self._handle_matches_record(original_handle, current):
                        raise AcpEmployeeStaleGeneration(
                            "new conversation started on a stale child generation"
                        )
            response = await record.child.new_session(self._new_request(employee))
            session_created = True
            candidate = ConversationSessionBinding(
                employee_id=employee.employee_id,
                acp_session_id=response.session_id,
                backend_key=employee.backend_key,
                binding_generation=old_binding.binding_generation + 1,
            )
            winner = await self._compare_and_swap_binding(old_binding, candidate)
            self._validate_binding(employee.employee_id, winner)
            if winner != candidate:
                deadline = (
                    asyncio.get_running_loop().time()
                    + ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS
                )
                adopted, _replay = await self._adopt_compaction_winner_under_reservation(
                    original_handle, winner, deadline
                )
                child_to_close = record.child
                return AcpEmployeeRecord(
                    employee=adopted.employee,
                    binding=adopted.binding,
                    child_generation=adopted.child_generation,
                    child=adopted.child,
                    record_identity=adopted.record_identity,
                )

            persisted = await self._resolve_binding(employee.employee_id)
            if persisted != candidate:
                raise AcpEmployeeBindingError(
                    "durable binding changed before new-conversation publication"
                )
            async with publication_gate:
                async with self._lock:
                    self._require_open_locked()
                    current = self._records.get(employee.employee_id)
                    if current is not record or not record.child.alive:
                        raise AcpEmployeeStaleGeneration(
                            "new conversation lost its child generation before publication"
                        )
                    replacement = AcpEmployeeRecord(
                        employee=employee,
                        binding=candidate,
                        child_generation=record.child_generation,
                        child=record.child,
                        record_identity=record.record_identity,
                    )
                    self._records[employee.employee_id] = replacement
                    self._accepted_callback_generations[employee.employee_id] = {
                        record.child_generation
                    }
                    return replacement
        except BaseException:
            if session_created:
                await self._invalidate_reserved_handle(original_handle)
                child_to_close = record.child
            raise
        finally:
            lifecycle_gate.release()
            if child_to_close is not None:
                self._detach_child_close(child_to_close)

    async def _initialize_record(
        self, employee: ConversationEmployee, generation: int
    ) -> AcpEmployeeRecord:
        lifecycle_gate = await self._lifecycle_mutation_gate(employee.employee_id)
        async with lifecycle_gate:
            return await self._initialize_record_under_lifecycle(employee, generation)

    async def _initialize_record_under_lifecycle(
        self, employee: ConversationEmployee, generation: int
    ) -> AcpEmployeeRecord:
        binding = await self._resolve_binding(employee.employee_id)
        if binding is not None:
            self._validate_binding(employee.employee_id, binding)

        child: AcpEmployeeChild | None = None
        try:
            definition = self._definition_for(employee.backend_key)
            child = await self._spawn_initialized_child(employee, generation, definition)
            if binding is None:
                response = await child.new_session(self._new_request(employee))
                candidate = ConversationSessionBinding(
                    employee_id=employee.employee_id,
                    acp_session_id=response.session_id,
                    backend_key=employee.backend_key,
                    binding_generation=1,
                )
                winner = await self._compare_and_swap_binding(None, candidate)
            elif binding.backend_key == employee.backend_key:
                await child.load_session(self._load_request(employee, binding.acp_session_id))
                winner = binding
                candidate = binding
            else:
                response = await child.new_session(self._new_request(employee))
                candidate = ConversationSessionBinding(
                    employee_id=employee.employee_id,
                    acp_session_id=response.session_id,
                    backend_key=employee.backend_key,
                    binding_generation=binding.binding_generation + 1,
                )
                winner = await self._compare_and_swap_binding(binding, candidate)

            self._validate_binding(employee.employee_id, winner)
            if winner != candidate:
                await child.close()
                child = None
                return await self._adopt_winner(employee, winner)
            return await self._publish(employee, winner, generation, child)
        except BaseException:
            if child is not None:
                await child.close()
            await self._discard_generation(employee.employee_id, generation)
            raise

    async def _adopt_winner(
        self, requested_employee: ConversationEmployee, winner: ConversationSessionBinding
    ) -> AcpEmployeeRecord:
        self._validate_binding(requested_employee.employee_id, winner)
        adopted_employee = requested_employee.model_copy(update={"backend_key": winner.backend_key})
        async with self._lock:
            self._require_open_locked()
            generation = self._allocate_generation_locked(requested_employee.employee_id)
        child: AcpEmployeeChild | None = None
        try:
            definition = self._definition_for(winner.backend_key)
            child = await self._spawn_initialized_child(adopted_employee, generation, definition)
            await child.load_session(self._load_request(adopted_employee, winner.acp_session_id))
            return await self._publish(adopted_employee, winner, generation, child)
        except BaseException:
            if child is not None:
                await child.close()
            await self._discard_generation(requested_employee.employee_id, generation)
            raise

    async def _spawn_initialized_child(
        self,
        employee: ConversationEmployee,
        generation: int,
        definition: AgentBackendDefinition,
    ) -> AcpEmployeeChild:
        factory = self._factories[definition.backend_key]

        async def guarded_ingress(payload: object) -> None:
            publication_gate = await self._publication_update_gate(employee.employee_id)
            async with publication_gate:
                async with self._lock:
                    allowed = self._accepted_callback_generations.get(employee.employee_id, set())
                    if generation not in allowed or self._closing:
                        raise AcpEmployeeStaleGeneration(
                            f"stale ACP update for generation {generation}"
                        )
                    record_identity = self._record_identity_by_generation.get(
                        (employee.employee_id, generation)
                    )
                    if record_identity is None:
                        raise AcpEmployeeStaleGeneration(
                            f"missing ACP source identity for generation {generation}"
                        )
                    source = ConversationIngressSource(
                        employee=employee,
                        child_generation=generation,
                        record_identity=record_identity,
                    )
                if self._source_aware_conversation_ingress is not None:
                    await self._source_aware_conversation_ingress(source, payload)
                else:
                    await self._conversation_ingress(payload)  # type: ignore[arg-type]

        async def guarded_death(error: BaseException | None) -> None:
            planned: _PlannedRetirement | None = None
            record_identity: object | None

            def settle_identity_locked() -> tuple[object | None, _PlannedRetirement | None]:
                identity = self._record_identity_by_generation.get(
                    (employee.employee_id, generation)
                )
                planned_key = (employee.employee_id, generation, id(identity))
                candidate = self._planned_retirements.get(planned_key)
                owned = (
                    self._planned_retirements.pop(planned_key)
                    if candidate is not None and candidate.record_identity is identity
                    else None
                )
                allowed = self._accepted_callback_generations.get(employee.employee_id)
                if allowed is not None:
                    allowed.discard(generation)
                current = self._records.get(employee.employee_id)
                if (
                    current is not None
                    and current.child_generation == generation
                    and current.record_identity is identity
                ):
                    self._records.pop(employee.employee_id, None)
                self._record_identity_by_generation.pop((employee.employee_id, generation), None)
                return identity, owned

            # A planned retirement or unpublished recovery candidate is owned by
            # the transaction which already holds lifecycle mutation. Its death
            # must settle without trying to reacquire publication-update.
            async with self._lock:
                identity = self._record_identity_by_generation.get(
                    (employee.employee_id, generation)
                )
                planned_key = (employee.employee_id, generation, id(identity))
                is_planned = planned_key in self._planned_retirements
                is_reserved = (
                    employee.employee_id,
                    generation,
                ) in self._reserved_callback_generations
                if is_planned or is_reserved:
                    record_identity, planned = settle_identity_locked()
                    self._reserved_callback_generations.discard((employee.employee_id, generation))
                else:
                    record_identity = None

            if not is_planned and not is_reserved:
                publication_gate = await self._publication_update_gate(employee.employee_id)
                async with publication_gate:
                    async with self._lock:
                        record_identity, planned = settle_identity_locked()

            if planned is not None:
                if not planned.settlement.done():
                    if error is None:
                        planned.settlement.set_result(None)
                    else:
                        planned.settlement.set_exception(error)
                return
            if is_reserved:
                return
            if self._source_aware_child_death_callback is not None and record_identity is not None:
                await self._source_aware_child_death_callback(
                    ConversationIngressSource(
                        employee=employee,
                        child_generation=generation,
                        record_identity=record_identity,
                    ),
                    error,
                )
            elif self._conversation_child_death_callback is not None:
                await self._conversation_child_death_callback(employee, generation, error)

        async def guarded_permission(
            request: RequestPermissionRequest,
        ) -> RequestPermissionResponse:
            if self._source_aware_permission_callback is None:
                return await self._permission_callback(request)
            try:
                handle = await self.resolve_runtime_handle_for_generation(
                    employee.employee_id, generation
                )
            except (AcpEmployeeRegistryError, ConversationRuntimeUnavailable):
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            return await self._source_aware_permission_callback(handle, request)

        child = await factory.create(
            employee,
            generation,
            guarded_ingress,  # type: ignore[arg-type]
            guarded_permission,
            guarded_death,
        )
        try:
            await child.initialize(build_panels_initialize_request(definition))
        except BaseException:
            with contextlib.suppress(BaseException):
                await child.close()
            raise
        return child

    async def _publish(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        generation: int,
        child: AcpEmployeeChild,
    ) -> AcpEmployeeRecord:
        persisted = await self._resolve_binding(employee.employee_id)
        if persisted != binding:
            raise AcpEmployeeBindingError("durable binding changed before child publication")
        publication_gate = await self._publication_update_gate(employee.employee_id)
        previous: AcpEmployeeRecord | None
        async with publication_gate:
            async with self._lock:
                self._require_open_locked()
                if generation not in self._accepted_callback_generations.get(
                    employee.employee_id, set()
                ):
                    raise AcpEmployeeStaleGeneration(
                        "candidate child generation died before publication"
                    )
                if not child.alive:
                    raise AcpEmployeeStaleGeneration("candidate child is not alive")
                previous = self._records.get(employee.employee_id)
                record = AcpEmployeeRecord(
                    employee=employee,
                    binding=binding,
                    child_generation=generation,
                    child=child,
                    record_identity=self._record_identity_by_generation[
                        (employee.employee_id, generation)
                    ],
                )
                self._records[employee.employee_id] = record
                self._accepted_callback_generations[employee.employee_id] = {generation}
        if previous is not None and previous.child is not child:
            await previous.child.close()
        return record

    async def shutdown(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            if self._closing:
                return
            self._closing = True
            self._accepted_callback_generations.clear()
            task_employee_ids: dict[asyncio.Task[Any], str] = {}
            for employee_id, initialization_task in self._initialization_tasks.items():
                task_employee_ids[initialization_task] = employee_id
            for employee_id, attach_task in self._attach_tasks.items():
                task_employee_ids[attach_task] = employee_id
            for employee_id, new_conversation_task in self._new_conversation_tasks.items():
                task_employee_ids[new_conversation_task] = employee_id
            records = tuple(self._records.values())
            prepared_states = tuple(self._prepared_compaction_captures.values())
            self._prepared_compaction_captures.clear()
            self._reserved_callback_generations.clear()
            self._record_identity_by_generation.clear()
        for prepared_state in prepared_states:
            if prepared_state.lifecycle_mutation_gate.locked():
                prepared_state.lifecycle_mutation_gate.release()
        operation_tasks = set(task_employee_ids)
        for operation_task in operation_tasks:
            operation_task.cancel()

        children_by_identity: dict[int, tuple[str, AcpEmployeeChild]] = {
            id(record.child): (record.employee.employee_id, record.child) for record in records
        }
        for prepared_state in prepared_states:
            original = prepared_state.prepared.original_handle
            children_by_identity[id(original.child)] = (
                original.employee.employee_id,
                original.child,
            )
            children_by_identity[id(prepared_state.candidate_child)] = (
                original.employee.employee_id,
                prepared_state.candidate_child,
            )
        close_task_children: dict[asyncio.Task[None], tuple[str, AcpEmployeeChild]] = {
            asyncio.create_task(child.close()): (employee_id, child)
            for employee_id, child in children_by_identity.values()
        }
        primary_tasks: set[asyncio.Task[Any]] = set(operation_tasks)
        primary_tasks.update(close_task_children)
        if not primary_tasks:
            return

        remaining = max(0.0, deadline - loop.time())
        graceful_budget = remaining / 2
        done, pending = await asyncio.wait(primary_tasks, timeout=graceful_budget)
        self._consume_shutdown_task_results(done)
        if not pending:
            return

        for pending_task in pending:
            pending_task.cancel()
        force_task_children: dict[asyncio.Task[None], tuple[str, AcpEmployeeChild]] = {}
        for close_task, (employee_id, child) in close_task_children.items():
            if close_task not in pending:
                continue
            force_close = getattr(child, "force_close", None)
            if force_close is not None:
                force_task_children[asyncio.create_task(force_close())] = (
                    employee_id,
                    child,
                )

        bounded_tasks = set(pending)
        bounded_tasks.update(force_task_children)
        remaining = max(0.0, deadline - loop.time())
        if bounded_tasks and remaining > 0:
            newly_done, still_pending = await asyncio.wait(bounded_tasks, timeout=remaining)
            self._consume_shutdown_task_results(newly_done)
        else:
            still_pending = bounded_tasks
        for unfinished_task in still_pending:
            unfinished_task.cancel()
            unfinished_task.add_done_callback(self._consume_late_shutdown_task_result)

        unfinished_employee_ids: set[str] = set()
        for unfinished_task in still_pending:
            unfinished_employee_id = task_employee_ids.get(unfinished_task)
            if unfinished_employee_id is not None:
                unfinished_employee_ids.add(unfinished_employee_id)
            close_child = close_task_children.get(unfinished_task)
            if close_child is not None:
                unfinished_employee_ids.add(close_child[0])
            force_child = force_task_children.get(unfinished_task)
            if force_child is not None:
                unfinished_employee_ids.add(force_child[0])
        if still_pending:
            raise AcpEmployeeRegistryShutdownError(tuple(sorted(unfinished_employee_ids)))

    async def _discard_generation(self, employee_id: str, generation: int) -> None:
        publication_gate = await self._publication_update_gate(employee_id)
        async with publication_gate:
            async with self._lock:
                accepted = self._accepted_callback_generations.get(employee_id)
                if accepted is not None:
                    accepted.discard(generation)
                self._record_identity_by_generation.pop((employee_id, generation), None)

    async def _retire_matching_record(self, employee_id: str, generation: int) -> None:
        publication_gate = await self._publication_update_gate(employee_id)
        async with publication_gate:
            async with self._lock:
                accepted = self._accepted_callback_generations.get(employee_id)
                if accepted is not None:
                    accepted.discard(generation)
                current = self._records.get(employee_id)
                if current is not None and current.child_generation == generation:
                    self._records.pop(employee_id, None)

    async def _lifecycle_mutation_gate(self, employee_id: str) -> asyncio.Lock:
        async with self._lock:
            return self._lifecycle_mutation_gates.setdefault(employee_id, asyncio.Lock())

    async def _publication_update_gate(self, employee_id: str) -> asyncio.Lock:
        async with self._lock:
            return self._publication_update_gates.setdefault(employee_id, asyncio.Lock())

    def _allocate_generation_locked(self, employee_id: str) -> int:
        generation = self._next_generation_by_employee.get(employee_id, 0) + 1
        self._next_generation_by_employee[employee_id] = generation
        self._accepted_callback_generations.setdefault(employee_id, set()).add(generation)
        self._record_identity_by_generation[(employee_id, generation)] = object()
        self._lifecycle_mutation_gates.setdefault(employee_id, asyncio.Lock())
        self._publication_update_gates.setdefault(employee_id, asyncio.Lock())
        return generation

    async def resolve_runtime_handle(
        self, employee_id: str, binding_generation: int
    ) -> ConversationRuntimeHandle:
        async with self._lock:
            record = self._records.get(employee_id)
            if (
                record is None
                or record.binding.binding_generation != binding_generation
                or not record.child.alive
            ):
                raise ConversationRuntimeUnavailable(
                    "matching ACP conversation runtime is not live"
                )
            return self._runtime_handle(record)

    async def resolve_runtime_handle_for_generation(
        self, employee_id: str, child_generation: int
    ) -> ConversationRuntimeHandle:
        async with self._lock:
            record = self._records.get(employee_id)
            if (
                record is None
                or record.child_generation != child_generation
                or not record.child.alive
            ):
                raise ConversationRuntimeUnavailable("matching ACP child generation is not live")
            return self._runtime_handle(record)

    async def acquire_runtime_lease(
        self, handle: ConversationRuntimeHandle
    ) -> ConversationRuntimeLease:
        publication_gate = await self._publication_update_gate(handle.employee.employee_id)
        async with publication_gate:
            async with self._lock:
                record = self._records.get(handle.employee.employee_id)
                if not self._handle_matches_record(handle, record):
                    raise ConversationRuntimeUnavailable(
                        "ACP runtime handle no longer identifies the live record"
                    )
                assert record is not None
                if not record.child.alive:
                    raise ConversationRuntimeUnavailable("ACP runtime child is no longer alive")
                return ConversationRuntimeLease(handle)

    async def retire_runtime_lease(self, lease: ConversationRuntimeLease, deadline: float) -> None:
        transaction_id = (
            f"cancel-retirement-{lease.handle.child_generation}-{id(lease.handle.record_identity)}"
        )
        await self._planned_retire_runtime(lease, transaction_id, deadline)

    async def replace_runtime_after_requested_cancel(
        self, lease: ConversationRuntimeLease, deadline: float
    ) -> RequestedCancelRuntimeReplacement:
        handle = lease.handle
        employee_id = handle.employee.employee_id
        lifecycle_gate = await self._lifecycle_mutation_gate(employee_id)
        publication_gate = await self._publication_update_gate(employee_id)
        await self._await_before_deadline(lifecycle_gate.acquire(), deadline)
        candidate_generation: int | None = None
        candidate_child: AcpEmployeeChild | None = None
        old_settlement: asyncio.Future[None] | None = None
        old_planned_key: tuple[str, int, int] | None = None
        try:
            async with publication_gate:
                async with self._lock:
                    self._require_open_locked()
                    record = self._records.get(employee_id)
                    if not self._handle_matches_record(handle, record) or not handle.child.alive:
                        raise ConversationRuntimeUnavailable(
                            "cannot replace a stale requested-cancel runtime lease"
                        )
                    old_planned_key = (
                        employee_id,
                        handle.child_generation,
                        id(handle.record_identity),
                    )
                    if old_planned_key in self._planned_retirements:
                        raise ConversationRuntimeUnavailable(
                            "requested-cancel runtime retirement is already planned"
                        )
                    old_settlement = asyncio.get_running_loop().create_future()
                    self._planned_retirements[old_planned_key] = _PlannedRetirement(
                        transaction_id=(
                            f"requested-cancel-{handle.child_generation}-"
                            f"{id(handle.record_identity)}"
                        ),
                        record_identity=handle.record_identity,
                        settlement=old_settlement,
                    )
                    accepted = self._accepted_callback_generations.get(employee_id)
                    if accepted is not None:
                        accepted.discard(handle.child_generation)

            await self._await_before_deadline(handle.child.close(), deadline)
            assert old_settlement is not None
            await self._await_before_deadline(asyncio.shield(old_settlement), deadline)

            async with self._lock:
                self._require_open_locked()
                candidate_generation = self._allocate_generation_locked(employee_id)
                self._reserved_callback_generations.add((employee_id, candidate_generation))

            candidate_child = await self._await_before_deadline(
                self._spawn_initialized_child(
                    handle.employee,
                    candidate_generation,
                    self._definition_for(handle.binding.backend_key),
                ),
                deadline,
            )
            captured: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

            async def private_ingress(
                item: SessionNotification | ProtocolUpdateRejectedPayload,
            ) -> None:
                captured.append(item)

            await self._await_before_deadline(
                candidate_child.capture_load_session(
                    self._load_request(handle.employee, handle.binding.acp_session_id),
                    cast(AcpConversationIngress, private_ingress),
                ),
                deadline,
            )
            durable = await self._await_before_deadline(
                self._resolve_binding(employee_id), deadline
            )
            if durable != handle.binding:
                raise AcpEmployeeBindingError(
                    "durable binding changed during requested-cancel replacement"
                )
            async with publication_gate:
                async with self._lock:
                    self._require_open_locked()
                    assert candidate_generation is not None
                    identity = self._record_identity_by_generation.get(
                        (employee_id, candidate_generation)
                    )
                    if (
                        identity is None
                        or candidate_generation
                        not in self._accepted_callback_generations.get(employee_id, set())
                        or not candidate_child.alive
                    ):
                        raise ConversationRuntimeUnavailable(
                            "requested-cancel replacement child died before publication"
                        )
                    replacement = AcpEmployeeRecord(
                        employee=handle.employee,
                        binding=handle.binding,
                        child_generation=candidate_generation,
                        child=candidate_child,
                        record_identity=identity,
                    )
                    self._records[employee_id] = replacement
                    self._accepted_callback_generations[employee_id] = {candidate_generation}
                    self._reserved_callback_generations.discard((employee_id, candidate_generation))
            return RequestedCancelRuntimeReplacement(
                replacement_handle=self._runtime_handle(replacement),
                replay=tuple(captured),
            )
        except BaseException:
            async with self._lock:
                if old_planned_key is not None:
                    planned = self._planned_retirements.pop(old_planned_key, None)
                    if planned is not None and planned.settlement.done():
                        with contextlib.suppress(BaseException):
                            planned.settlement.result()
                self._invalidate_reserved_handle_locked(handle)
                if candidate_generation is not None:
                    accepted = self._accepted_callback_generations.get(employee_id)
                    if accepted is not None:
                        accepted.discard(candidate_generation)
                    self._record_identity_by_generation.pop(
                        (employee_id, candidate_generation), None
                    )
                    self._reserved_callback_generations.discard((employee_id, candidate_generation))
                    current = self._records.get(employee_id)
                    if current is not None and current.child_generation == candidate_generation:
                        self._records.pop(employee_id, None)
            if candidate_child is not None and candidate_child.alive:
                self._detach_child_close(candidate_child)
            if handle.child.alive:
                self._detach_child_close(handle.child)
            raise
        finally:
            lifecycle_gate.release()

    async def fail_runtime_handle(self, handle: ConversationRuntimeHandle) -> None:
        """Make one exact runtime generation unusable without waiting on child shutdown."""
        publication_gate = await self._publication_update_gate(handle.employee.employee_id)
        async with publication_gate:
            async with self._lock:
                if not self._handle_matches_record(
                    handle, self._records.get(handle.employee.employee_id)
                ):
                    return
                self._invalidate_reserved_handle_locked(handle)
        self._detach_child_close(handle.child)

    async def prepare_compaction_capture(
        self,
        lease: ConversationRuntimeLease,
        capture_transaction_id: str,
        deadline: float,
        capture_timeout_seconds: float = ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
        compacted_boundaries: tuple[ConversationCompactionBoundaryProvenance, ...] = (),
    ) -> PreparedCompactionCapture:
        if not capture_transaction_id.strip():
            raise ValueError("capture_transaction_id must not be blank")
        handle = lease.handle
        lifecycle_gate = await self._lifecycle_mutation_gate(handle.employee.employee_id)
        publication_gate = await self._publication_update_gate(handle.employee.employee_id)
        try:
            await self._await_before_deadline(lifecycle_gate.acquire(), deadline)
        except ConversationWaitDeadlineExpired as error:
            raise ConversationCompactionCaptureDeadlineExpired(
                phase="hub admission",
                capture_timeout_seconds=capture_timeout_seconds,
                durable_binding_disposition="stayed",
                original_binding_generation=handle.binding.binding_generation,
                generation_fatal=False,
            ) from error
        source_validated = False
        initial_publication_gate_acquisition_pending = True
        candidate_generation: int | None = None
        candidate_child: AcpEmployeeChild | None = None
        capture_phase: ConversationCompactionCapturePhase = "fork"
        try:
            await self._acquire_lock_before_deadline(publication_gate, deadline)
            initial_publication_gate_acquisition_pending = False
            try:
                async with self._lock:
                    self._require_open_locked()
                    record = self._records.get(handle.employee.employee_id)
                    if not self._handle_matches_record(handle, record):
                        raise ConversationRuntimeUnavailable(
                            "cannot prepare compaction on a stale ACP runtime lease"
                        )
                    if not handle.child.alive:
                        raise ConversationRuntimeUnavailable(
                            "cannot prepare compaction on a dead ACP child"
                        )
                    source_validated = True
            finally:
                publication_gate.release()
            if self._resolve_compaction_boundaries is None:
                raise ConversationRuntimeUnavailable(
                    "compaction provenance resolver is not composed"
                )
            expected_compaction_boundaries = cast(
                tuple[ConversationCompactionBoundaryProvenance, ...],
                await self._await_before_deadline(
                    self._resolve_compaction_boundaries(handle.binding), deadline
                ),
            )
            if not handle.child.supports_session_fork:
                raise ConversationRuntimeUnavailable("ACP child does not advertise session/fork")
            original_load = self._load_request(handle.employee, handle.binding.acp_session_id)
            fork_response = await self._await_before_deadline(
                handle.child.fork_session(
                    ForkSessionRequest(
                        session_id=handle.binding.acp_session_id,
                        cwd=original_load.cwd,
                        additional_directories=original_load.additional_directories,
                        mcp_servers=original_load.mcp_servers,
                        field_meta=original_load.field_meta,
                    )
                ),
                deadline,
            )
            fork_session_id = fork_response.session_id.strip()
            if not fork_session_id or fork_session_id == handle.binding.acp_session_id:
                raise ConversationRuntimeUnavailable(
                    "session/fork did not return a distinct successor session"
                )
            candidate_binding = ConversationSessionBinding(
                employee_id=handle.employee.employee_id,
                acp_session_id=fork_session_id,
                backend_key=handle.binding.backend_key,
                binding_generation=handle.binding.binding_generation + 1,
            )
            captured: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

            async def private_ingress(
                item: SessionNotification | ProtocolUpdateRejectedPayload,
            ) -> None:
                captured.append(item)

            async with self._lock:
                self._require_open_locked()
                candidate_generation = self._allocate_generation_locked(handle.employee.employee_id)
                self._reserved_callback_generations.add(
                    (handle.employee.employee_id, candidate_generation)
                )
                candidate_record_identity = self._record_identity_by_generation[
                    (handle.employee.employee_id, candidate_generation)
                ]
            candidate_child = await self._await_before_deadline(
                self._spawn_initialized_child(
                    handle.employee,
                    candidate_generation,
                    self._definition_for(handle.binding.backend_key),
                ),
                deadline,
            )
            capture_phase = "private fork load"
            await self._await_before_deadline(
                candidate_child.capture_load_session(
                    self._load_request(handle.employee, fork_session_id),
                    cast(AcpConversationIngress, private_ingress),
                ),
                deadline,
            )
            await self._acquire_lock_before_deadline(publication_gate, deadline)
            try:
                async with self._lock:
                    record = self._records.get(handle.employee.employee_id)
                    if not self._handle_matches_record(handle, record):
                        raise ConversationRuntimeUnavailable(
                            "ACP runtime changed during compaction preparation"
                        )
                    if not handle.child.alive:
                        raise ConversationRuntimeUnavailable(
                            "ACP child died during compaction preparation"
                        )
                    if (
                        candidate_generation
                        not in self._accepted_callback_generations.get(
                            handle.employee.employee_id, set()
                        )
                        or self._record_identity_by_generation.get(
                            (handle.employee.employee_id, candidate_generation)
                        )
                        is not candidate_record_identity
                        or not candidate_child.alive
                    ):
                        raise ConversationRuntimeUnavailable(
                            "compaction candidate child died before preparation completed"
                        )
                    transaction_identity = object()
                    prepared = PreparedCompactionCapture(
                        transaction_identity=transaction_identity,
                        original_handle=handle,
                        candidate_binding=candidate_binding,
                        captured_replay=tuple(captured),
                        expected_compaction_boundaries=expected_compaction_boundaries,
                        candidate_compaction_boundaries=compacted_boundaries,
                        deadline=deadline,
                        capture_timeout_seconds=capture_timeout_seconds,
                    )
                    self._prepared_compaction_captures[transaction_identity] = (
                        _PreparedCompactionCaptureState(
                            prepared=prepared,
                            capture_transaction_id=capture_transaction_id,
                            lifecycle_mutation_gate=lifecycle_gate,
                            candidate_generation=candidate_generation,
                            candidate_child=candidate_child,
                            candidate_record_identity=candidate_record_identity,
                        )
                    )
            finally:
                publication_gate.release()
            return prepared
        except BaseException as capture_error:
            source_requires_retirement = source_validated
            async with self._lock:
                if candidate_generation is not None:
                    self._discard_reserved_generation_locked(
                        handle.employee.employee_id, candidate_generation
                    )
                if initial_publication_gate_acquisition_pending and self._handle_matches_record(
                    handle,
                    self._records.get(handle.employee.employee_id),
                ):
                    source_requires_retirement = True
                if source_requires_retirement:
                    self._invalidate_reserved_handle_locked(handle)
            if candidate_child is not None:
                self._detach_child_close(candidate_child)
            if source_requires_retirement:
                lifecycle_gate.release()
                self._detach_child_close(handle.child)
                if isinstance(capture_error, ConversationWaitDeadlineExpired):
                    raise ConversationCompactionCaptureDeadlineExpired(
                        phase=capture_phase,
                        capture_timeout_seconds=capture_timeout_seconds,
                        durable_binding_disposition="stayed",
                        original_binding_generation=handle.binding.binding_generation,
                        generation_fatal=True,
                    ) from capture_error
                if isinstance(capture_error, asyncio.CancelledError):
                    raise
                raise ConversationCompactionCaptureFailed(
                    phase=capture_phase,
                    primary_error=capture_error,
                    generation_fatal=True,
                ) from capture_error
            lifecycle_gate.release()
            if isinstance(
                capture_error, ConversationCompactionCaptureDeadlineExpired
            ) or isinstance(capture_error, asyncio.CancelledError):
                raise
            raise ConversationCompactionCaptureFailed(
                phase=capture_phase,
                primary_error=capture_error,
                generation_fatal=False,
            ) from capture_error

    async def commit_compaction_capture(
        self,
        prepared: PreparedCompactionCapture,
        deadline: float,
    ) -> CompactionCaptureTransition:
        state = await self._begin_compaction_settlement(prepared, deadline)
        original = prepared.original_handle
        child_to_close: AcpEmployeeChild | None = None
        child_to_fail: AcpEmployeeChild | None = None
        candidate_published = False
        cas_winner: ConversationSessionBinding | None = None
        cas_response_received = False
        cas_error: BaseException | None = None
        try:
            if self._compare_and_swap_compaction is None:
                raise ConversationCompactionCaptureFailed(
                    phase="durable CAS/resolve",
                    primary_error=ConversationRuntimeUnavailable(
                        "compaction binding CAS is not composed"
                    ),
                    generation_fatal=True,
                )
            try:
                cas_winner = await self._await_before_deadline(
                    self._compare_and_swap_compaction(
                        original.binding,
                        prepared.expected_compaction_boundaries,
                        prepared.candidate_binding,
                        prepared.candidate_compaction_boundaries,
                    ),
                    deadline,
                )
                cas_response_received = True
            except ConversationWaitDeadlineExpired as error:
                child_to_fail = original.child
                raise ConversationCompactionCaptureDeadlineExpired(
                    phase="durable CAS/resolve",
                    capture_timeout_seconds=prepared.capture_timeout_seconds,
                    durable_binding_disposition="unresolved",
                    original_binding_generation=(original.binding.binding_generation),
                    generation_fatal=True,
                ) from error
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                # A response failure can follow a committed CAS. Resolve the
                # durable mirror before deciding which state won.
                cas_error = error
            try:
                durable = await self._await_before_deadline(
                    self._resolve_binding(original.employee.employee_id), deadline
                )
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                child_to_fail = original.child
                if isinstance(error, ConversationWaitDeadlineExpired):
                    disposition: ConversationCompactionDurableBindingDisposition = "unresolved"
                    if cas_response_received:
                        disposition = "stayed" if cas_winner == original.binding else "committed"
                    raise ConversationCompactionCaptureDeadlineExpired(
                        phase="durable CAS/resolve",
                        capture_timeout_seconds=prepared.capture_timeout_seconds,
                        durable_binding_disposition=disposition,
                        original_binding_generation=(original.binding.binding_generation),
                        generation_fatal=True,
                    ) from error
                if cas_error is not None:
                    raise ConversationCompactionCaptureFailed(
                        phase="durable CAS/resolve",
                        primary_error=cas_error,
                        recovery_phase="durable CAS/resolve",
                        recovery_error=error,
                        generation_fatal=True,
                    ) from error
                raise ConversationCompactionCaptureFailed(
                    phase="durable CAS/resolve",
                    primary_error=error,
                    generation_fatal=True,
                ) from error
            durable_compaction_boundaries: tuple[ConversationCompactionBoundaryProvenance, ...] = ()
            if durable is not None:
                if self._resolve_compaction_boundaries is None:
                    raise ConversationCompactionCaptureFailed(
                        phase="durable CAS/resolve",
                        primary_error=ConversationRuntimeUnavailable(
                            "compaction provenance resolver is not composed"
                        ),
                        generation_fatal=True,
                    )
                try:
                    durable_compaction_boundaries = cast(
                        tuple[ConversationCompactionBoundaryProvenance, ...],
                        await self._await_before_deadline(
                            self._resolve_compaction_boundaries(durable), deadline
                        ),
                    )
                except asyncio.CancelledError:
                    raise
                except BaseException as error:
                    child_to_fail = original.child
                    raise ConversationCompactionCaptureFailed(
                        phase="durable CAS/resolve",
                        primary_error=error,
                        generation_fatal=True,
                    ) from error
            if durable == prepared.candidate_binding:
                if durable_compaction_boundaries != prepared.candidate_compaction_boundaries:
                    provenance_error = ConversationRuntimeGenerationFatal(
                        "durable compaction winner has different boundary provenance"
                    )
                    child_to_fail = original.child
                    raise ConversationCompactionCaptureFailed(
                        phase="durable CAS/resolve",
                        primary_error=provenance_error,
                        generation_fatal=True,
                    ) from provenance_error
                publication_gate = await self._publication_update_gate(
                    original.employee.employee_id
                )
                try:
                    await self._acquire_lock_before_deadline(publication_gate, deadline)
                except ConversationWaitDeadlineExpired as error:
                    raise ConversationCompactionCaptureDeadlineExpired(
                        phase="durable CAS/resolve",
                        capture_timeout_seconds=prepared.capture_timeout_seconds,
                        durable_binding_disposition="committed",
                        original_binding_generation=(original.binding.binding_generation),
                        generation_fatal=True,
                    ) from error
                try:
                    async with self._lock:
                        self._require_open_locked()
                        record = self._records.get(original.employee.employee_id)
                        if (
                            not self._handle_matches_record(original, record)
                            or self._record_identity_by_generation.get(
                                (
                                    original.employee.employee_id,
                                    state.candidate_generation,
                                )
                            )
                            is not state.candidate_record_identity
                            or state.candidate_generation
                            not in self._accepted_callback_generations.get(
                                original.employee.employee_id, set()
                            )
                            or not state.candidate_child.alive
                        ):
                            self._invalidate_reserved_handle_locked(original)
                            child_to_fail = original.child
                            publication_error = ConversationRuntimeGenerationFatal(
                                "compaction winner lost its exact runtime before publication"
                            )
                            raise ConversationCompactionCaptureFailed(
                                phase="durable CAS/resolve",
                                primary_error=publication_error,
                                generation_fatal=True,
                            )
                        replacement = AcpEmployeeRecord(
                            employee=original.employee,
                            binding=prepared.candidate_binding,
                            child_generation=state.candidate_generation,
                            child=state.candidate_child,
                            record_identity=state.candidate_record_identity,
                        )
                        self._records[original.employee.employee_id] = replacement
                        self._accepted_callback_generations[original.employee.employee_id] = {
                            state.candidate_generation
                        }
                        self._reserved_callback_generations.discard(
                            (
                                original.employee.employee_id,
                                state.candidate_generation,
                            )
                        )
                        self._record_identity_by_generation.pop(
                            (
                                original.employee.employee_id,
                                original.child_generation,
                            ),
                            None,
                        )
                finally:
                    publication_gate.release()
                candidate_published = True
                child_to_close = original.child
                return CompactionCaptureTransition(
                    replacement_handle=self._runtime_handle(replacement),
                    replay=prepared.captured_replay,
                    fork_won=True,
                )
            if durable == original.binding:
                binding_error: BaseException = cas_error or ConversationRuntimeUnavailable(
                    "compaction binding CAS did not commit"
                )
                child_to_fail = original.child
                raise ConversationCompactionCaptureFailed(
                    phase="durable CAS/resolve",
                    primary_error=binding_error,
                    generation_fatal=True,
                ) from binding_error
            if durable is None:
                child_to_fail = original.child
                missing_binding_error = ConversationRuntimeGenerationFatal(
                    "durable conversation binding disappeared during compaction"
                )
                raise ConversationCompactionCaptureFailed(
                    phase="durable CAS/resolve",
                    primary_error=missing_binding_error,
                    generation_fatal=True,
                ) from missing_binding_error
            try:
                self._validate_binding(original.employee.employee_id, durable)
                if (
                    durable.binding_generation != original.binding.binding_generation + 1
                    or durable.acp_session_id == original.binding.acp_session_id
                ):
                    raise AcpEmployeeBindingError(
                        "external compaction winner is not the exact successor binding"
                    )
            except BaseException as error:
                child_to_fail = original.child
                raise ConversationCompactionCaptureFailed(
                    phase="durable CAS/resolve",
                    primary_error=error,
                    generation_fatal=True,
                ) from error
            try:
                await self._discard_compaction_candidate(state, deadline)
            except ConversationWaitDeadlineExpired as error:
                raise ConversationCompactionCaptureDeadlineExpired(
                    phase="winner adoption",
                    capture_timeout_seconds=prepared.capture_timeout_seconds,
                    durable_binding_disposition="committed",
                    original_binding_generation=(original.binding.binding_generation),
                    generation_fatal=True,
                ) from error
            try:
                adopted_handle, replay = await self._adopt_compaction_winner_under_reservation(
                    original, durable, deadline
                )
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                child_to_fail = original.child
                if isinstance(error, ConversationWaitDeadlineExpired):
                    raise ConversationCompactionCaptureDeadlineExpired(
                        phase="winner adoption",
                        capture_timeout_seconds=prepared.capture_timeout_seconds,
                        durable_binding_disposition="committed",
                        original_binding_generation=(original.binding.binding_generation),
                        generation_fatal=True,
                    ) from error
                raise ConversationCompactionCaptureFailed(
                    phase="winner adoption",
                    primary_error=error,
                    generation_fatal=True,
                ) from error
            child_to_close = original.child
            return CompactionCaptureTransition(
                replacement_handle=adopted_handle,
                replay=replay,
                fork_won=False,
            )
        finally:
            if not candidate_published:
                await self._settle_unpublished_compaction_capture(state, deadline)
            else:
                await self._finish_compaction_settlement(state)
                if child_to_close is not None:
                    self._detach_child_close(child_to_close)
                if child_to_fail is not None:
                    self._detach_child_close(child_to_fail)

    async def abort_compaction_capture(
        self,
        prepared: PreparedCompactionCapture,
        deadline: float,
    ) -> None:
        state = await self._begin_compaction_settlement(prepared, deadline)
        await self._settle_unpublished_compaction_capture(state, deadline)

    async def _begin_compaction_settlement(
        self,
        prepared: PreparedCompactionCapture,
        deadline: float,
    ) -> _PreparedCompactionCaptureState:
        if deadline != prepared.deadline:
            raise ConversationRuntimeUnavailable(
                "compaction settlement did not use its original deadline"
            )
        async with self._lock:
            state = self._prepared_compaction_captures.get(prepared.transaction_identity)
            if state is None or state.prepared is not prepared or state.settling:
                raise ConversationRuntimeUnavailable(
                    "prepared compaction transaction is stale or already settled"
                )
            state.settling = True
            return state

    async def _finish_compaction_settlement(self, state: _PreparedCompactionCaptureState) -> None:
        async with self._lock:
            current = self._prepared_compaction_captures.get(state.prepared.transaction_identity)
            if current is state:
                self._prepared_compaction_captures.pop(state.prepared.transaction_identity, None)
        if state.lifecycle_mutation_gate.locked():
            state.lifecycle_mutation_gate.release()

    async def _settle_unpublished_compaction_capture(
        self,
        state: _PreparedCompactionCaptureState,
        deadline: float,
    ) -> None:
        original = state.prepared.original_handle
        publication_gate: asyncio.Lock | None = None
        publication_acquired = False
        cleanup_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()
        if current_task is None or not current_task.cancelling():
            try:
                publication_gate = await self._publication_update_gate(
                    original.employee.employee_id
                )
                await self._acquire_lock_before_deadline(publication_gate, deadline)
                publication_acquired = True
            except ConversationWaitDeadlineExpired:
                pass
            except asyncio.CancelledError as error:
                cleanup_cancellation = error
        try:
            async with self._lock:
                self._invalidate_reserved_handle_locked(original)
                self._discard_reserved_generation_locked(
                    original.employee.employee_id, state.candidate_generation
                )
                current = self._prepared_compaction_captures.get(
                    state.prepared.transaction_identity
                )
                if current is state:
                    self._prepared_compaction_captures.pop(
                        state.prepared.transaction_identity, None
                    )
        finally:
            if publication_acquired:
                assert publication_gate is not None
                publication_gate.release()
            if state.lifecycle_mutation_gate.locked():
                state.lifecycle_mutation_gate.release()
            self._detach_child_close(original.child)
            self._detach_child_close(state.candidate_child)
        if cleanup_cancellation is not None:
            raise cleanup_cancellation

    async def _discard_compaction_candidate(
        self, state: _PreparedCompactionCaptureState, deadline: float
    ) -> None:
        employee_id = state.prepared.original_handle.employee.employee_id
        publication_gate = await self._publication_update_gate(employee_id)
        await self._acquire_lock_before_deadline(publication_gate, deadline)
        try:
            async with self._lock:
                self._discard_reserved_generation_locked(employee_id, state.candidate_generation)
        finally:
            publication_gate.release()
        if state.candidate_child.alive:
            self._detach_child_close(state.candidate_child)

    async def _discard_reserved_generation(self, employee_id: str, generation: int) -> None:
        publication_gate = await self._publication_update_gate(employee_id)
        async with publication_gate:
            async with self._lock:
                self._discard_reserved_generation_locked(employee_id, generation)

    def _discard_reserved_generation_locked(self, employee_id: str, generation: int) -> None:
        accepted = self._accepted_callback_generations.get(employee_id)
        if accepted is not None:
            accepted.discard(generation)
        self._record_identity_by_generation.pop((employee_id, generation), None)
        self._reserved_callback_generations.discard((employee_id, generation))
        current = self._records.get(employee_id)
        if current is not None and current.child_generation == generation:
            self._records.pop(employee_id, None)

    async def _adopt_compaction_winner_under_reservation(
        self,
        original: ConversationRuntimeHandle,
        winner: ConversationSessionBinding,
        deadline: float,
    ) -> tuple[
        ConversationRuntimeHandle,
        tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
    ]:
        adopted_employee = original.employee.model_copy(update={"backend_key": winner.backend_key})
        async with self._lock:
            self._require_open_locked()
            if not self._handle_matches_record(
                original, self._records.get(original.employee.employee_id)
            ):
                raise ConversationRuntimeUnavailable(
                    "compaction loser no longer owns its original runtime"
                )
            generation = self._allocate_generation_locked(original.employee.employee_id)
            self._reserved_callback_generations.add((original.employee.employee_id, generation))
        child: AcpEmployeeChild | None = None
        try:
            definition = self._definition_for(winner.backend_key)
            child = await self._await_before_deadline(
                self._spawn_initialized_child(adopted_employee, generation, definition),
                deadline,
            )
            replay: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

            async def private_ingress(
                item: SessionNotification | ProtocolUpdateRejectedPayload,
            ) -> None:
                replay.append(item)

            await self._await_before_deadline(
                child.capture_load_session(
                    self._load_request(adopted_employee, winner.acp_session_id),
                    cast(AcpConversationIngress, private_ingress),
                ),
                deadline,
            )
            persisted = await self._await_before_deadline(
                self._resolve_binding(original.employee.employee_id), deadline
            )
            if persisted != winner:
                raise AcpEmployeeBindingError(
                    "durable winner changed before compaction publication"
                )
            publication_gate = await self._publication_update_gate(original.employee.employee_id)
            await self._acquire_lock_before_deadline(publication_gate, deadline)
            try:
                async with self._lock:
                    self._require_open_locked()
                    if not self._handle_matches_record(
                        original, self._records.get(original.employee.employee_id)
                    ):
                        raise ConversationRuntimeUnavailable(
                            "compaction loser changed before winner publication"
                        )
                    if (
                        generation
                        not in self._accepted_callback_generations.get(
                            original.employee.employee_id, set()
                        )
                        or not child.alive
                    ):
                        raise ConversationRuntimeUnavailable(
                            "compaction winner child died before publication"
                        )
                    record = AcpEmployeeRecord(
                        employee=adopted_employee,
                        binding=winner,
                        child_generation=generation,
                        child=child,
                        record_identity=self._record_identity_by_generation[
                            (original.employee.employee_id, generation)
                        ],
                    )
                    self._records[original.employee.employee_id] = record
                    self._accepted_callback_generations[original.employee.employee_id] = {
                        generation
                    }
                    self._reserved_callback_generations.discard(
                        (original.employee.employee_id, generation)
                    )
            finally:
                publication_gate.release()
            return self._runtime_handle(record), tuple(replay)
        except BaseException:
            async with self._lock:
                accepted = self._accepted_callback_generations.get(original.employee.employee_id)
                if accepted is not None:
                    accepted.discard(generation)
                self._record_identity_by_generation.pop(
                    (original.employee.employee_id, generation), None
                )
                self._reserved_callback_generations.discard(
                    (original.employee.employee_id, generation)
                )
            if child is not None:
                self._detach_child_close(child)
            raise

    async def _invalidate_reserved_handle(self, handle: ConversationRuntimeHandle) -> None:
        publication_gate = await self._publication_update_gate(handle.employee.employee_id)
        async with publication_gate:
            async with self._lock:
                self._invalidate_reserved_handle_locked(handle)

    def _invalidate_reserved_handle_locked(self, handle: ConversationRuntimeHandle) -> None:
        current = self._records.get(handle.employee.employee_id)
        if self._handle_matches_record(handle, current):
            self._records.pop(handle.employee.employee_id, None)
        accepted = self._accepted_callback_generations.get(handle.employee.employee_id)
        if accepted is not None:
            accepted.discard(handle.child_generation)
        self._record_identity_by_generation.pop(
            (handle.employee.employee_id, handle.child_generation), None
        )

    @staticmethod
    async def _acquire_lock_before_deadline(lock: asyncio.Lock, deadline: float) -> None:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=remaining)
        except TimeoutError as error:
            raise ConversationWaitDeadlineExpired(
                "ACP runtime transition exceeded its deadline"
            ) from error

    @staticmethod
    async def _await_before_deadline(operation: Awaitable[_Awaited], deadline: float) -> _Awaited:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        task = asyncio.ensure_future(operation)
        try:
            done, _pending = await asyncio.wait({task}, timeout=remaining)
        except BaseException:
            if not task.done():
                task.cancel()
                task.add_done_callback(AcpEmployeeRegistry._consume_late_future_result)
            raise
        if task not in done:
            task.cancel()
            task.add_done_callback(AcpEmployeeRegistry._consume_late_future_result)
            raise ConversationWaitDeadlineExpired("ACP runtime transition exceeded its deadline")
        return task.result()

    @staticmethod
    def _consume_late_future_result(task: asyncio.Future[Any]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.result()

    @staticmethod
    def _detach_child_close(child: AcpEmployeeChild) -> None:
        task = asyncio.create_task(child.close())
        task.add_done_callback(AcpEmployeeRegistry._consume_late_future_result)

    def strategy_for_lease(self, lease: ConversationRuntimeLease) -> Any:
        if not lease.handle.child.alive:
            raise ConversationRuntimeUnavailable("ACP runtime child is no longer alive")
        return GenerationBoundBackendTurnStrategy(
            self,
            lease,
            lease.handle.definition.turn_strategy,
        )

    async def _planned_retire_runtime(
        self,
        lease: ConversationRuntimeLease,
        transaction_id: str,
        deadline: float,
    ) -> None:
        handle = lease.handle
        lifecycle_gate = await self._lifecycle_mutation_gate(handle.employee.employee_id)
        publication_gate = await self._publication_update_gate(handle.employee.employee_id)
        acquired_lifecycle_gate = False
        key: tuple[str, int, int] | None = None
        try:
            await self._await_before_deadline(lifecycle_gate.acquire(), deadline)
            acquired_lifecycle_gate = True
            async with publication_gate:
                async with self._lock:
                    record = self._records.get(handle.employee.employee_id)
                    if not self._handle_matches_record(handle, record):
                        raise ConversationRuntimeUnavailable(
                            "cannot retire a stale ACP runtime lease"
                        )
                    key = (
                        handle.employee.employee_id,
                        handle.child_generation,
                        id(handle.record_identity),
                    )
                    if key in self._planned_retirements:
                        raise ConversationRuntimeUnavailable(
                            "ACP runtime retirement is already planned"
                        )
                    settlement: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                    self._planned_retirements[key] = _PlannedRetirement(
                        transaction_id=transaction_id,
                        record_identity=handle.record_identity,
                        settlement=settlement,
                    )
                    accepted = self._accepted_callback_generations.get(handle.employee.employee_id)
                    if accepted is not None:
                        accepted.discard(handle.child_generation)
            await self._await_before_deadline(handle.child.close(), deadline)
            await self._await_before_deadline(asyncio.shield(settlement), deadline)
        except BaseException:
            async with publication_gate:
                async with self._lock:
                    if key is not None:
                        planned = self._planned_retirements.pop(key, None)
                        if planned is not None and planned.settlement.done():
                            with contextlib.suppress(BaseException):
                                planned.settlement.result()
                    self._invalidate_reserved_handle_locked(handle)
            if handle.child.alive:
                self._detach_child_close(handle.child)
            raise
        finally:
            if acquired_lifecycle_gate:
                lifecycle_gate.release()

    async def _retire_unpublished_generation(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        generation: int,
        child: AcpEmployeeChild,
        transaction_id: str,
        deadline: float,
    ) -> None:
        del binding
        lifecycle_gate = await self._lifecycle_mutation_gate(employee.employee_id)
        publication_gate = await self._publication_update_gate(employee.employee_id)
        async with lifecycle_gate:
            async with publication_gate:
                async with self._lock:
                    identity = self._record_identity_by_generation.get(
                        (employee.employee_id, generation)
                    )
                    if identity is None:
                        return
                    settlement: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                    self._planned_retirements[(employee.employee_id, generation, id(identity))] = (
                        _PlannedRetirement(
                            transaction_id=transaction_id,
                            record_identity=identity,
                            settlement=settlement,
                        )
                    )
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait_for(child.close(), timeout=remaining)
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait_for(asyncio.shield(settlement), timeout=remaining)

    def _runtime_handle(self, record: AcpEmployeeRecord) -> ConversationRuntimeHandle:
        return ConversationRuntimeHandle(
            employee=record.employee,
            binding=record.binding,
            child_generation=record.child_generation,
            child=record.child,
            definition=self._definition_for(record.binding.backend_key),
            record_identity=record.record_identity,
        )

    @staticmethod
    def _handle_matches_record(
        handle: ConversationRuntimeHandle,
        record: AcpEmployeeRecord | None,
    ) -> bool:
        return (
            record is not None
            and record.employee == handle.employee
            and record.binding == handle.binding
            and record.child_generation == handle.child_generation
            and record.child is handle.child
            and record.record_identity is handle.record_identity
        )

    def _require_open_locked(self) -> None:
        if self._closing:
            raise AcpEmployeeRegistryClosed("ACP employee registry is closing")

    def _definition_for(self, backend_key: str) -> AgentBackendDefinition:
        try:
            return self._definitions[backend_key]
        except KeyError:
            raise AcpEmployeeBindingError(
                f"unknown ACP backend definition {backend_key!r}"
            ) from None

    @staticmethod
    def _record_matches(record: AcpEmployeeRecord, employee: ConversationEmployee) -> bool:
        return (
            record.child.alive
            and record.employee == employee
            and record.binding.backend_key == employee.backend_key
        )

    @staticmethod
    def _consume_shutdown_task_results(
        tasks: set[asyncio.Task[Any]],
    ) -> None:
        for task in tasks:
            if task.cancelled():
                continue
            with contextlib.suppress(BaseException):
                task.result()

    @staticmethod
    def _consume_late_shutdown_task_result(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.result()

    @staticmethod
    def _validate_binding(employee_id: str, binding: ConversationSessionBinding) -> None:
        if binding.employee_id != employee_id:
            raise AcpEmployeeBindingError(
                "durable conversation binding belongs to another employee"
            )

    def _working_directories(self, employee: ConversationEmployee) -> tuple[Path, tuple[Path, ...]]:
        definition = self._definition_for(employee.backend_key)
        cwd = definition.working_directory_for(employee)
        additional = tuple(root for root in employee.workspace_roots if root != cwd)
        return cwd, additional

    def _new_request(self, employee: ConversationEmployee) -> NewSessionRequest:
        cwd, additional = self._working_directories(employee)
        return NewSessionRequest(
            cwd=str(cwd),
            additional_directories=[str(root) for root in additional],
            mcp_servers=[],
        )

    def _load_request(self, employee: ConversationEmployee, session_id: str) -> LoadSessionRequest:
        cwd, additional = self._working_directories(employee)
        return LoadSessionRequest(
            cwd=str(cwd),
            session_id=session_id,
            additional_directories=[str(root) for root in additional],
            mcp_servers=[],
        )
