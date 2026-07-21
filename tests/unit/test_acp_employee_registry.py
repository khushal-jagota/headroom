from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentCapabilities,
    AgentThoughtChunk,
    CancelNotification,
    ForkSessionRequest,
    ForkSessionResponse,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
)
from tests.support.acp_conformance import (
    assert_callback_wire_order,
    assert_durable_refresh,
    assert_load_replay,
    mutate_probe_evidence,
)
from tests.support.acp_in_memory_binding_repository import (
    InMemoryAcpBindingRepository,
)
from tests.support.acp_runtime_subject import ProductionAcp01ConformanceSubject

from planner.conversation import (
    AcpEmployeeBindingError,
    AcpEmployeeRegistry,
    AcpEmployeeRegistryShutdownError,
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ConversationCompactionCaptureDeadlineExpired,
    ConversationCompactionCaptureFailed,
    ConversationEmployee,
    ConversationRuntimeHandle,
    ConversationRuntimeLease,
    ConversationRuntimeUnavailable,
    ConversationSessionBinding,
    EmployeeBackendBuildContext,
    EmployeeBackendCatalog,
    ProtocolUpdateRejectedPayload,
    ReverseServiceCapabilities,
    static_employee_backend_registration,
)


class _TurnStrategy:
    def classify_replay(
        self, _binding: Any, replay: tuple[Any, ...], _boundaries: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        return replay

    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _definition(backend_key: str, cwd_index: int = 0) -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key=backend_key,
        argv=(f"/{backend_key}", "acp"),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name=f"{backend_key}-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(supports_steer=False, observes_compaction=False),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False, terminal=False, permission=True
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[cwd_index],
        turn_strategy=_TurnStrategy(),
    )


def _employee(employee_id: str = "employee-a", backend_key: str = "alpha") -> ConversationEmployee:
    return ConversationEmployee(
        employee_id=employee_id,
        entity_kind="ticket",
        entity_id=f"ticket-{employee_id}",
        workspace_roots=(Path("/work/first"), Path("/work/second")),
        backend_key=backend_key,
    )


@dataclass
class _FakeChild:
    generation: int
    definition: AgentBackendDefinition
    update_ingress: Any
    death_callback: Any
    alive: bool = True
    initialize_gate: asyncio.Event | None = None
    load_gate: asyncio.Event | None = None
    new_session_gate: asyncio.Event | None = None
    fail_load: bool = False
    supports_fork: bool = True
    close_error: BaseException | None = None
    operation_observer: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        self.new_requests: list[NewSessionRequest] = []
        self.load_requests: list[LoadSessionRequest] = []
        self.initialize_requests: list[InitializeRequest] = []
        self.fork_requests: list[ForkSessionRequest] = []
        self.private_load_requests: list[LoadSessionRequest] = []
        self.private_replay_by_session: dict[str, tuple[SessionNotification, ...]] = {}
        self.fail_private_load_session_ids: set[str] = set()
        self.private_load_errors_by_session: dict[str, BaseException] = {}
        self.fork_gate: asyncio.Event | None = None
        self.private_load_gate: asyncio.Event | None = None
        self._session_number = 0
        self._fork_number = 0
        self._death_settled = False
        self.current_session_id: str | None = None
        self.initialize_started = asyncio.Event()
        self.load_started = asyncio.Event()
        self.new_session_started = asyncio.Event()
        self.fork_started = asyncio.Event()
        self.private_load_started = asyncio.Event()

    @property
    def supports_session_fork(self) -> bool:
        return self.supports_fork

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        if self.operation_observer is not None:
            self.operation_observer("initialize")
        self.initialize_requests.append(request)
        self.initialize_started.set()
        if self.initialize_gate is not None:
            await self.initialize_gate.wait()
        return InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name=self.definition.expected_agent_name,
                version=self.definition.expected_agent_version,
            ),
        )

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse:
        if self.operation_observer is not None:
            self.operation_observer("new_session")
        self.new_requests.append(request)
        self.new_session_started.set()
        if self.new_session_gate is not None:
            await self.new_session_gate.wait()
        self._session_number += 1
        session_id = f"{self.definition.backend_key}-{self.generation}-{self._session_number}"
        self.current_session_id = session_id
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        if self.operation_observer is not None:
            self.operation_observer("load_session")
        self.load_requests.append(request)
        self.load_started.set()
        if self.load_gate is not None:
            await self.load_gate.wait()
        if self.fail_load:
            raise RuntimeError("scripted load failure")
        self.current_session_id = request.session_id
        return LoadSessionResponse()

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        if self.operation_observer is not None:
            self.operation_observer("fork_session")
        self.fork_requests.append(request)
        self.fork_started.set()
        if self.fork_gate is not None:
            await self.fork_gate.wait()
        if not self.supports_fork:
            raise RuntimeError("session/fork not supported")
        self._fork_number += 1
        return ForkSessionResponse(session_id=f"{request.session_id}-fork-{self._fork_number}")

    async def capture_load_session(
        self,
        request: LoadSessionRequest,
        private_ingress: Any,
    ) -> LoadSessionResponse:
        if self.operation_observer is not None:
            self.operation_observer("capture_load_session")
        self.private_load_requests.append(request)
        self.current_session_id = request.session_id
        self.private_load_started.set()
        if self.private_load_gate is not None:
            await self.private_load_gate.wait()
        scripted_error = self.private_load_errors_by_session.get(request.session_id)
        if scripted_error is not None:
            raise scripted_error
        if request.session_id in self.fail_private_load_session_ids:
            raise RuntimeError("scripted private load failure")
        for notification in self.private_replay_by_session.get(request.session_id, ()):
            await private_ingress(notification)
        return LoadSessionResponse()

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        del request
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        if self.operation_observer is not None:
            self.operation_observer("close")
        if not self.alive:
            return
        self.alive = False
        await self._settle_death(self.close_error)

    async def crash(self, cause: BaseException | None = None) -> None:
        self.alive = False
        await self._settle_death(cause or RuntimeError("crash"))

    async def _settle_death(self, cause: BaseException | None) -> None:
        if self._death_settled:
            return
        self._death_settled = True
        await self.death_callback(cause)


class _FakeFactory:
    def __init__(self, definition: AgentBackendDefinition) -> None:
        self.definition = definition
        self.children: list[_FakeChild] = []
        self.creation_gate: asyncio.Event | None = None
        self.creation_started = asyncio.Event()
        self.initialize_gate: asyncio.Event | None = None
        self.load_gate: asyncio.Event | None = None
        self.new_session_gate: asyncio.Event | None = None
        self.private_load_gate: asyncio.Event | None = None
        self.fail_load = False
        self.close_error: BaseException | None = None
        self.private_replay_by_session: dict[str, tuple[SessionNotification, ...]] = {}
        self.fail_private_load_session_ids: set[str] = set()
        self.private_load_errors_by_session: dict[str, BaseException] = {}
        self.operation_observer: Callable[[str], None] | None = None

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _FakeChild:
        del employee, permission_callback
        self.creation_started.set()
        if self.creation_gate is not None:
            await self.creation_gate.wait()
        child = _FakeChild(
            generation=generation,
            definition=self.definition,
            update_ingress=update_ingress,
            death_callback=death_callback,
            initialize_gate=self.initialize_gate,
            load_gate=self.load_gate,
            new_session_gate=self.new_session_gate,
            fail_load=self.fail_load,
            close_error=self.close_error,
            operation_observer=self.operation_observer,
        )
        child.private_replay_by_session.update(self.private_replay_by_session)
        child.fail_private_load_session_ids.update(self.fail_private_load_session_ids)
        child.private_load_errors_by_session.update(self.private_load_errors_by_session)
        child.private_load_gate = self.private_load_gate
        self.children.append(child)
        return child


class _CancellationResistantCloseChild(_FakeChild):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.force_close_calls = 0

    async def close(self) -> None:
        self.close_started.set()
        try:
            await self.close_release.wait()
        except asyncio.CancelledError:
            await self.close_release.wait()
        await super().close()

    async def force_close(self) -> None:
        self.force_close_calls += 1
        self.alive = False


class _CancellationResistantCloseFactory(_FakeFactory):
    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _CancellationResistantCloseChild:
        del employee, permission_callback
        child = _CancellationResistantCloseChild(
            generation=generation,
            definition=self.definition,
            update_ingress=update_ingress,
            death_callback=death_callback,
        )
        self.children.append(child)
        return child


class _CancellationResistantFactory(_FakeFactory):
    def __init__(self, definition: AgentBackendDefinition) -> None:
        super().__init__(definition)
        self.release = asyncio.Event()

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _FakeChild:
        self.creation_started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue
        return await super().create(
            employee,
            generation,
            update_ingress,
            permission_callback,
            death_callback,
        )


async def _permission(
    request: RequestPermissionRequest,
) -> RequestPermissionResponse:
    del request
    return RequestPermissionResponse(outcome={"outcome": "cancelled"})


def _registry(
    definitions: dict[str, AgentBackendDefinition],
    factories: dict[str, _FakeFactory],
    repository: InMemoryAcpBindingRepository,
    *,
    ingress: Any | None = None,
    resolve: Any | None = None,
    compare_and_swap: Any | None = None,
    child_death: Any | None = None,
) -> AcpEmployeeRegistry:
    async def discard(
        payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        del payload

    ordinary_compare_and_swap = compare_and_swap or repository.compare_and_swap

    async def compare_compaction(
        expected: ConversationSessionBinding,
        expected_boundaries: tuple[Any, ...],
        candidate: ConversationSessionBinding,
        candidate_boundaries: tuple[Any, ...],
    ) -> ConversationSessionBinding:
        if not candidate_boundaries:
            return await ordinary_compare_and_swap(expected, candidate)
        return await repository.compare_and_swap_compaction(
            expected,
            expected_boundaries,
            candidate,
            candidate_boundaries,
        )

    catalog = EmployeeBackendCatalog(
        tuple(
            static_employee_backend_registration(definition, factories[backend_key])
            for backend_key, definition in definitions.items()
        )
    )
    return AcpEmployeeRegistry(
        backend_catalog=catalog,
        materialized_backends=catalog.materialize(
            EmployeeBackendBuildContext(data_directory=Path.cwd())
        ),
        resolve_binding=resolve or repository.resolve,
        compare_and_swap_binding=ordinary_compare_and_swap,
        resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
        compare_and_swap_compaction=compare_compaction,
        conversation_ingress=ingress or discard,
        permission_callback=_permission,
        conversation_child_death_callback=child_death,
    )


def test_concurrent_first_demand_coalesces_and_persists_generation_one() -> None:
    async def exercise() -> None:
        definition = _definition("alpha", cwd_index=1)
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)

        first, second = await asyncio.gather(
            registry.get_or_spawn(_employee()), registry.get_or_spawn(_employee())
        )
        assert first is second
        assert len(factory.children) == 1
        assert first.binding.binding_generation == 1
        assert await repository.resolve("employee-a") == first.binding
        request = factory.children[0].new_requests[0]
        assert request.cwd == "/work/second"
        assert request.additional_directories == ["/work/first"]
        assert request.mcp_servers == []
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_retires_exact_lease_and_privately_loads_same_binding_fresh() -> (
    None
):
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        ordinary: list[object] = []

        async def ingress(payload: object) -> None:
            ordinary.append(payload)

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            ingress=ingress,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            original.employee.employee_id, original.binding.binding_generation
        )
        lease = await registry.acquire_runtime_lease(handle)
        replay = SessionNotification(
            session_id=original.binding.acp_session_id,
            update=AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=TextContentBlock(type="text", text="replayed"),
            ),
        )
        factory.private_replay_by_session[original.binding.acp_session_id] = (replay,)

        replacement = await registry.replace_runtime_after_requested_cancel(
            lease, asyncio.get_running_loop().time() + 1
        )

        assert original.child.alive is False
        assert replacement.replacement_handle.binding == original.binding
        assert replacement.replacement_handle.child_generation == (original.child_generation + 1)
        assert replacement.replacement_handle.child is factory.children[1]
        assert replacement.replacement_handle.record_identity is not original.record_identity
        assert factory.children[1].private_load_requests[0].session_id == (
            original.binding.acp_session_id
        )
        assert replacement.replay == (replay,)
        assert ordinary == []
        assert await repository.resolve(original.employee.employee_id) == original.binding
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_rejects_stale_record_identity_without_retiring_current() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        stale_handle = ConversationRuntimeHandle(
            employee=handle.employee,
            binding=handle.binding,
            child_generation=handle.child_generation,
            child=handle.child,
            definition=handle.definition,
            record_identity=object(),
        )

        with pytest.raises(
            ConversationRuntimeUnavailable,
            match="stale requested-cancel runtime lease",
        ):
            await registry.replace_runtime_after_requested_cancel(
                ConversationRuntimeLease(stale_handle),
                asyncio.get_running_loop().time() + 1,
            )

        assert original.child.alive is True
        assert len(factory.children) == 1
        assert await registry.resolve_runtime_handle("employee-a", 1) == handle
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_planned_close_error_is_transaction_owned_and_next_demand_recovers() -> (
    None
):
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        ordinary_deaths: list[tuple[ConversationEmployee, int, BaseException | None]] = []

        async def child_died(
            employee: ConversationEmployee,
            generation: int,
            error: BaseException | None,
        ) -> None:
            ordinary_deaths.append((employee, generation, error))

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            child_death=child_died,
        )
        original = await registry.get_or_spawn(_employee())
        original.child.close_error = RuntimeError("planned close failed")
        handle = await registry.resolve_runtime_handle("employee-a", 1)

        with pytest.raises(RuntimeError, match="planned close failed"):
            await registry.replace_runtime_after_requested_cancel(
                await registry.acquire_runtime_lease(handle),
                asyncio.get_running_loop().time() + 1,
            )

        assert ordinary_deaths == []
        replacement = await registry.get_or_spawn(_employee())
        assert replacement.binding == original.binding
        assert replacement.child_generation == 2
        assert replacement.child is not original.child
        assert await repository.resolve("employee-a") == original.binding
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_private_load_failure_leaves_no_reusable_generation() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        factory.fail_private_load_session_ids.add(original.binding.acp_session_id)
        handle = await registry.resolve_runtime_handle("employee-a", 1)

        with pytest.raises(RuntimeError, match="scripted private load failure"):
            await registry.replace_runtime_after_requested_cancel(
                await registry.acquire_runtime_lease(handle),
                asyncio.get_running_loop().time() + 1,
            )
        await asyncio.sleep(0)

        assert original.child.alive is False
        assert factory.children[1].alive is False
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        factory.fail_private_load_session_ids.clear()
        recovered = await registry.get_or_spawn(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == 3
        assert recovered.child not in (original.child, factory.children[1])
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_waits_for_admitted_old_ingress_before_retirement() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        ingress_started = asyncio.Event()
        release_ingress = asyncio.Event()

        async def held_ingress(payload: object) -> None:
            del payload
            ingress_started.set()
            await release_ingress.wait()

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            ingress=held_ingress,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        lease = await registry.acquire_runtime_lease(handle)
        admitted = asyncio.create_task(original.child.update_ingress(object()))
        await ingress_started.wait()
        recovery = asyncio.create_task(
            registry.replace_runtime_after_requested_cancel(
                lease,
                asyncio.get_running_loop().time() + 1,
            )
        )
        await asyncio.sleep(0)

        assert not recovery.done()
        assert original.child.alive is True
        assert len(factory.children) == 1
        release_ingress.set()
        await admitted
        replacement = await asyncio.wait_for(recovery, timeout=1)
        assert replacement.replacement_handle.child_generation == 2
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_rejects_candidate_death_during_fresh_initialize() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        await registry.get_or_spawn(_employee())
        initialize_gate = asyncio.Event()
        factory.initialize_gate = initialize_gate
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        recovery = asyncio.create_task(
            registry.replace_runtime_after_requested_cancel(
                await registry.acquire_runtime_lease(handle),
                asyncio.get_running_loop().time() + 1,
            )
        )
        while len(factory.children) < 2:
            await asyncio.sleep(0)
        candidate = factory.children[1]
        await candidate.initialize_started.wait()

        await candidate.crash(RuntimeError("candidate died during initialize"))
        initialize_gate.set()
        with pytest.raises(
            ConversationRuntimeUnavailable,
            match="replacement child died before publication",
        ):
            await recovery

        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_rejects_durable_binding_drift_after_private_load() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        await registry.get_or_spawn(_employee())
        private_load_gate = asyncio.Event()
        factory.private_load_gate = private_load_gate
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        recovery = asyncio.create_task(
            registry.replace_runtime_after_requested_cancel(
                await registry.acquire_runtime_lease(handle),
                asyncio.get_running_loop().time() + 1,
            )
        )
        while len(factory.children) < 2:
            await asyncio.sleep(0)
        await factory.children[1].private_load_started.wait()
        drifted = ConversationSessionBinding(
            employee_id="employee-a",
            acp_session_id="drifted-session",
            backend_key="alpha",
            binding_generation=2,
        )
        await repository.seed(drifted)
        private_load_gate.set()

        with pytest.raises(AcpEmployeeBindingError, match="durable binding changed"):
            await recovery
        await asyncio.sleep(0)
        assert factory.children[1].alive is False
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_replacement_private_load_obeys_one_hard_deadline() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        factory.private_load_gate = asyncio.Event()
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.03

        with pytest.raises(TimeoutError, match="runtime transition"):
            await registry.replace_runtime_after_requested_cancel(
                await registry.acquire_runtime_lease(handle), deadline
            )
        assert loop.time() < deadline + 0.04
        await asyncio.sleep(0)
        assert factory.children[1].alive is False
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        factory.private_load_gate = None
        recovered = await registry.get_or_spawn(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == 3
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_exact_retirement_deadline_invalidates_cancellation_resistant_generation() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _CancellationResistantCloseFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        child = factory.children[0]
        assert isinstance(child, _CancellationResistantCloseChild)
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.03
        retirement = asyncio.create_task(
            registry.retire_runtime_lease(await registry.acquire_runtime_lease(handle), deadline)
        )
        await child.close_started.wait()
        done, _pending = await asyncio.wait({retirement}, timeout=0.12)
        if retirement not in done:
            child.close_release.set()
            with contextlib.suppress(BaseException):
                await retirement
            pytest.fail("exact retirement exceeded its original absolute deadline")

        with pytest.raises(TimeoutError, match="runtime transition"):
            retirement.result()
        assert loop.time() < deadline + 0.04
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        child.close_release.set()
        await asyncio.sleep(0)
        recovered = await registry.get_or_spawn(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == 2
        recovered_child = factory.children[1]
        assert isinstance(recovered_child, _CancellationResistantCloseChild)
        recovered_child.close_release.set()
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_forks_source_then_privately_loads_and_publishes_fresh_child() -> None:
    async def exercise() -> None:
        definition = _definition("alpha", cwd_index=1)
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        ordinary_ingress: list[object] = []

        async def record_ordinary(item: object) -> None:
            ordinary_ingress.append(item)

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            ingress=record_ordinary,
        )
        original = await registry.get_or_spawn(_employee())
        original_handle = await registry.resolve_runtime_handle(
            original.employee.employee_id,
            original.binding.binding_generation,
        )
        lease = await registry.acquire_runtime_lease(original_handle)
        source_child = factory.children[0]
        fork_session_id = f"{original.binding.acp_session_id}-fork-1"
        replay = SessionNotification(
            session_id=fork_session_id,
            update=AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=TextContentBlock(type="text", text="private summary"),
            ),
        )
        factory.private_replay_by_session[fork_session_id] = (replay,)
        deadline = asyncio.get_running_loop().time() + 1

        prepared = await registry.prepare_compaction_capture(
            lease,
            capture_transaction_id="capture-1",
            deadline=deadline,
        )

        assert source_child.fork_requests == [
            ForkSessionRequest(
                session_id=original.binding.acp_session_id,
                cwd="/work/second",
                additional_directories=["/work/first"],
                mcp_servers=[],
            )
        ]
        assert source_child.private_load_requests == []
        assert len(factory.children) == 2
        candidate_child = factory.children[1]
        assert [request.session_id for request in candidate_child.private_load_requests] == [
            fork_session_id
        ]
        assert candidate_child.load_requests == []
        assert prepared.captured_replay == (replay,)
        assert prepared.original_handle == original_handle
        assert prepared.candidate_binding == ConversationSessionBinding(
            employee_id="employee-a",
            acp_session_id=fork_session_id,
            backend_key="alpha",
            binding_generation=original.binding.binding_generation + 1,
        )
        assert await repository.resolve("employee-a") == original.binding
        assert ordinary_ingress == []

        transition = await registry.commit_compaction_capture(prepared, deadline)

        assert transition.fork_won is True
        assert transition.replay == (replay,)
        replacement = transition.replacement_handle
        assert replacement.binding == prepared.candidate_binding
        assert replacement.child is candidate_child
        assert replacement.child_generation == original.child_generation + 1
        assert replacement.record_identity is not original.record_identity
        assert await repository.resolve("employee-a") == replacement.binding
        await asyncio.sleep(0)
        assert source_child.alive is False
        with pytest.raises(Exception, match="matching ACP conversation runtime"):
            await registry.resolve_runtime_handle("employee-a", original.binding.binding_generation)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_private_load_does_not_block_ordinary_source_publication() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        factory.private_load_gate = asyncio.Event()
        repository = InMemoryAcpBindingRepository()
        published: list[SessionNotification] = []

        async def ingress(item: object) -> None:
            assert isinstance(item, SessionNotification)
            published.append(item)

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=ingress)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 1
        preparation = asyncio.create_task(
            registry.prepare_compaction_capture(
                await registry.acquire_runtime_lease(handle),
                "capture-publication-progress",
                deadline,
            )
        )
        while len(factory.children) < 2:
            await asyncio.sleep(0)
        await factory.children[1].private_load_started.wait()
        ordinary = SessionNotification(
            session_id=original.binding.acp_session_id,
            update=AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=TextContentBlock(type="text", text="ordinary N"),
            ),
        )
        publication = asyncio.create_task(factory.children[0].update_ingress(ordinary))
        try:
            done, _pending = await asyncio.wait({publication}, timeout=0.05)
            assert publication in done
            assert published == [ordinary]
        finally:
            factory.private_load_gate.set()
            prepared = await preparation
            await registry.abort_compaction_capture(prepared, deadline)
            with contextlib.suppress(BaseException):
                await publication
            await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_initial_publication_gate_expiry_retires_exact_source() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def ingress(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=ingress)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        lease = await registry.acquire_runtime_lease(handle)
        source_child = factory.children[0]
        publication = asyncio.create_task(
            source_child.update_ingress(
                SessionNotification(
                    session_id=original.binding.acp_session_id,
                    update=AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        content=TextContentBlock(type="text", text="held N"),
                    ),
                )
            )
        )
        await sink_entered.wait()
        deadline = asyncio.get_running_loop().time() + 0.04
        preparation = asyncio.create_task(
            registry.prepare_compaction_capture(
                lease,
                "capture-held-initial-publication-gate",
                deadline,
                capture_timeout_seconds=0.04,
            )
        )
        try:
            done, _pending = await asyncio.wait({preparation}, timeout=0.15)
            assert preparation in done, "initial gate expiry re-waited on the held gate"
            with pytest.raises(ConversationCompactionCaptureDeadlineExpired) as failure:
                preparation.result()
            assert failure.value.phase == "fork"
            assert failure.value.generation_fatal is True
            assert len(factory.children) == 1
            assert registry._prepared_compaction_captures == {}  # noqa: SLF001
            assert registry._reserved_callback_generations == set()  # noqa: SLF001
            assert not registry._lifecycle_mutation_gates[  # noqa: SLF001
                "employee-a"
            ].locked()
            with pytest.raises(ConversationRuntimeUnavailable):
                await registry.resolve_runtime_handle("employee-a", 1)
            await asyncio.sleep(0)
            assert source_child.alive is False
        finally:
            release_sink.set()
            with contextlib.suppress(BaseException):
                await preparation
            await publication
        assert await repository.resolve("employee-a") == original.binding
        recovered = await registry.attach(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == 2
        assert factory.children[1].load_requests[0].session_id == (original.binding.acp_session_id)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_preparation_gate_expiry_fails_closed_without_gate_rewait() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        factory.private_load_gate = asyncio.Event()
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def ingress(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=ingress)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 0.04
        preparation = asyncio.create_task(
            registry.prepare_compaction_capture(
                await registry.acquire_runtime_lease(handle),
                "capture-held-preparation-gate",
                deadline,
                capture_timeout_seconds=0.04,
            )
        )
        while len(factory.children) < 2:
            await asyncio.sleep(0)
        await factory.children[1].private_load_started.wait()
        publication = asyncio.create_task(
            factory.children[0].update_ingress(
                SessionNotification(
                    session_id=original.binding.acp_session_id,
                    update=AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        content=TextContentBlock(type="text", text="held N"),
                    ),
                )
            )
        )
        await sink_entered.wait()
        factory.private_load_gate.set()
        try:
            done, _pending = await asyncio.wait({preparation}, timeout=0.15)
            assert preparation in done, "preparation outlived its absolute deadline"
            with pytest.raises(ConversationCompactionCaptureDeadlineExpired) as failure:
                preparation.result()
            assert failure.value.phase == "private fork load"
            assert failure.value.generation_fatal is True
            assert registry._prepared_compaction_captures == {}  # noqa: SLF001
            assert registry._reserved_callback_generations == set()  # noqa: SLF001
            lifecycle_gate = registry._lifecycle_mutation_gates[  # noqa: SLF001
                "employee-a"
            ]
            assert not lifecycle_gate.locked()
            with pytest.raises(ConversationRuntimeUnavailable):
                await registry.resolve_runtime_handle("employee-a", 1)
            await asyncio.sleep(0)
            assert [child.alive for child in factory.children] == [False, False]
        finally:
            release_sink.set()
            with contextlib.suppress(BaseException):
                await preparation
            await publication
        assert await repository.resolve("employee-a") == original.binding
        recovered = await registry.attach(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == 3
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_publication_cancellation_fails_closed_without_gate_rewait() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def ingress(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=ingress)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-cancel-held-publication",
            deadline,
        )
        publication = asyncio.create_task(
            factory.children[0].update_ingress(
                SessionNotification(
                    session_id=original.binding.acp_session_id,
                    update=AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        content=TextContentBlock(type="text", text="held N"),
                    ),
                )
            )
        )
        await sink_entered.wait()
        commit = asyncio.create_task(registry.commit_compaction_capture(prepared, deadline))
        while await repository.resolve("employee-a") != prepared.candidate_binding:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        commit.cancel()
        try:
            done, _pending = await asyncio.wait({commit}, timeout=0.15)
            assert commit in done, "cancelled publication re-waited on the held gate"
            with pytest.raises(asyncio.CancelledError):
                commit.result()
            assert registry._prepared_compaction_captures == {}  # noqa: SLF001
            assert registry._reserved_callback_generations == set()  # noqa: SLF001
            lifecycle_gate = registry._lifecycle_mutation_gates[  # noqa: SLF001
                "employee-a"
            ]
            assert not lifecycle_gate.locked()
            await asyncio.sleep(0)
            assert [child.alive for child in factory.children] == [False, False]
        finally:
            release_sink.set()
            with contextlib.suppress(BaseException):
                await commit
            await publication
        assert await repository.resolve("employee-a") == prepared.candidate_binding
        recovered = await registry.attach(_employee())
        assert recovered.binding == prepared.candidate_binding
        assert recovered.child_generation == 3
        assert factory.children[2].load_requests[0].session_id == (
            prepared.candidate_binding.acp_session_id
        )
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_lifecycle_external_io_never_holds_publication_update_gate() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry_holder: list[AcpEmployeeRegistry] = []
        observed_operations: list[str] = []

        def observe(operation: str) -> None:
            if registry_holder:
                gate = registry_holder[0]._publication_update_gates.get(  # noqa: SLF001
                    "employee-a"
                )
                assert gate is None or not gate.locked(), operation
            observed_operations.append(operation)

        factory.operation_observer = observe

        await repository.seed(
            ConversationSessionBinding(
                employee_id="employee-a",
                backend_key="alpha",
                acp_session_id="alpha-existing",
                binding_generation=1,
            )
        )

        async def resolve(
            employee_id: str,
        ) -> ConversationSessionBinding | None:
            observe("resolve_binding")
            return await repository.resolve(employee_id)

        async def compare_and_swap(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            observe("compare_and_swap_binding")
            return await repository.compare_and_swap(expected, candidate)

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            resolve=resolve,
            compare_and_swap=compare_and_swap,
        )
        registry_holder.append(registry)
        record = await registry.get_or_spawn(_employee())
        await registry.attach(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 1
        requested_cancel = await registry.replace_runtime_after_requested_cancel(
            await registry.acquire_runtime_lease(handle), deadline
        )
        handle = requested_cancel.replacement_handle
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-no-publication-io",
            deadline,
        )
        transition = await registry.commit_compaction_capture(prepared, deadline)
        replacement = await registry.new_conversation(_employee())
        await registry.retire_runtime_lease(
            await registry.acquire_runtime_lease(
                await registry.resolve_runtime_handle(
                    "employee-a", replacement.binding.binding_generation
                )
            ),
            deadline,
        )

        assert record.binding.binding_generation == 1
        assert requested_cancel.replacement_handle.binding.binding_generation == 1
        assert transition.replacement_handle.binding.binding_generation == 2
        assert replacement.binding.binding_generation == 3
        assert {
            "initialize",
            "new_session",
            "load_session",
            "capture_load_session",
            "fork_session",
            "close",
            "resolve_binding",
            "compare_and_swap_binding",
        } <= set(observed_operations)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_abort_invalidates_source_and_candidate_then_fresh_attaches_n() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            "employee-a", original.binding.binding_generation
        )
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle), "capture-abort", deadline
        )

        await registry.abort_compaction_capture(prepared, deadline)

        source_child, candidate_child = factory.children
        assert source_child.private_load_requests == []
        assert [request.session_id for request in candidate_child.private_load_requests] == [
            prepared.candidate_binding.acp_session_id
        ]
        assert await repository.resolve("employee-a") == original.binding
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", original.binding.binding_generation)
        with pytest.raises(ConversationRuntimeUnavailable, match="already settled"):
            await registry.abort_compaction_capture(prepared, deadline)
        attached = await asyncio.wait_for(registry.attach(_employee()), timeout=1)
        assert attached.binding == original.binding
        assert attached.child_generation == candidate_child.generation + 1
        assert factory.children[2].load_requests[0].session_id == (original.binding.acp_session_id)
        await asyncio.sleep(0)
        assert source_child.alive is False
        assert candidate_child.alive is False
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_ambiguous_cas_resolves_committed_candidate() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()

        async def ambiguous_cas(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            await repository.seed(candidate)
            raise RuntimeError("response lost after durable commit")

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=ambiguous_cas,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            "employee-a", original.binding.binding_generation
        )
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle), "capture-ambiguous", deadline
        )

        transition = await registry.commit_compaction_capture(prepared, deadline)

        assert transition.fork_won is True
        assert transition.replacement_handle.binding == prepared.candidate_binding
        assert transition.replacement_handle.child is factory.children[1]
        assert transition.replacement_handle.child_generation == (original.child_generation + 1)
        assert await repository.resolve("employee-a") == prepared.candidate_binding
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_cas_exception_with_original_durable_invalidates_source() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()

        async def failed_cas(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            raise RuntimeError("CAS failed before commit")

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=failed_cas,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle), "capture-cas-failure", deadline
        )

        with pytest.raises(
            ConversationRuntimeUnavailable,
            match=("durable CAS/resolve: RuntimeError: CAS failed before commit"),
        ):
            await registry.commit_compaction_capture(prepared, deadline)

        assert await repository.resolve("employee-a") == original.binding
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        attached = await asyncio.wait_for(registry.attach(_employee()), timeout=1)
        assert attached.binding == original.binding
        assert attached.child_generation == original.child_generation + 2
        assert factory.children[2].load_requests[0].session_id == (original.binding.acp_session_id)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_cas_expiry_reports_unresolved_binding_without_second_budget() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        hold_replacement_cas = asyncio.Event()

        async def hanging_cas(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            await hold_replacement_cas.wait()
            return candidate

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=hanging_cas,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 0.02
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-cas-timeout",
            deadline,
            capture_timeout_seconds=0.02,
        )
        expected_reason = (
            "Conversation compaction capture exceeded its configured 0.02-second budget "
            "during durable CAS/resolve; durable binding disposition was unresolved at "
            "deadline between generation 1 and generation 2; fresh attach must resolve "
            "the authoritative database binding; the uncertain runtime child was "
            "invalidated; fresh attach will resolve and load the authoritative binding"
        )

        with pytest.raises(
            ConversationCompactionCaptureDeadlineExpired,
            match=expected_reason,
        ) as failure:
            await registry.commit_compaction_capture(prepared, deadline)

        assert failure.value.durable_binding_disposition == "unresolved"
        assert failure.value.generation_fatal is True
        assert await repository.resolve("employee-a") == original.binding
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        recovered = await asyncio.wait_for(registry.get_or_spawn(_employee()), timeout=1)
        assert recovered.binding == original.binding
        assert recovered.child_generation == original.child_generation + 2
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_mirror_is_exact_before_runtime_record_publication() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry_holder: list[AcpEmployeeRegistry] = []
        original_binding: ConversationSessionBinding | None = None
        mirror_checked_before_record = False

        async def resolve(
            employee_id: str,
        ) -> ConversationSessionBinding | None:
            nonlocal mirror_checked_before_record
            binding = await repository.resolve(employee_id)
            if (
                binding is not None
                and original_binding is not None
                and binding.binding_generation == original_binding.binding_generation + 1
            ):
                old_handle = await registry_holder[0].resolve_runtime_handle(
                    employee_id, original_binding.binding_generation
                )
                assert old_handle.binding == original_binding
                mirror_checked_before_record = True
            return binding

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            resolve=resolve,
        )
        registry_holder.append(registry)
        original = await registry.get_or_spawn(_employee())
        original_binding = original.binding
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle), "capture-mirror", deadline
        )

        transition = await registry.commit_compaction_capture(prepared, deadline)

        assert mirror_checked_before_record is True
        assert transition.replacement_handle.binding == prepared.candidate_binding
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_cas_loser_adopts_durable_winner_with_private_replay() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        external_winner: ConversationSessionBinding | None = None

        async def losing_cas(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            nonlocal external_winner
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            external_winner = candidate.model_copy(
                update={"acp_session_id": "external-compaction-winner"}
            )
            await repository.seed(external_winner)
            return external_winner

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=losing_cas,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            "employee-a", original.binding.binding_generation
        )
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle), "capture-loser", deadline
        )

        transition = await registry.commit_compaction_capture(prepared, deadline)

        assert external_winner is not None
        assert transition.fork_won is False
        assert transition.replacement_handle.binding == external_winner
        assert transition.replacement_handle.child_generation == (original.child_generation + 2)
        assert transition.replacement_handle.child is factory.children[2]
        assert [request.session_id for request in factory.children[2].private_load_requests] == [
            "external-compaction-winner"
        ]
        assert factory.children[2].load_requests == []
        assert factory.children[1].alive is False
        await asyncio.sleep(0)
        assert original.child.alive is False
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_external_winner_publication_gate_expiry_fails_closed() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()
        external_winner: ConversationSessionBinding | None = None

        async def ingress(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        async def losing_cas(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            nonlocal external_winner
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            external_winner = candidate.model_copy(
                update={"acp_session_id": "held-external-winner"}
            )
            await repository.seed(external_winner)
            return external_winner

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            ingress=ingress,
            compare_and_swap=losing_cas,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 0.1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-held-external-publication",
            deadline,
            capture_timeout_seconds=0.1,
        )
        factory.private_load_gate = asyncio.Event()
        commit = asyncio.create_task(registry.commit_compaction_capture(prepared, deadline))
        while len(factory.children) < 3:
            await asyncio.sleep(0)
        await factory.children[2].private_load_started.wait()
        publication = asyncio.create_task(
            factory.children[0].update_ingress(
                SessionNotification(
                    session_id=original.binding.acp_session_id,
                    update=AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        content=TextContentBlock(type="text", text="held N"),
                    ),
                )
            )
        )
        await sink_entered.wait()
        factory.private_load_gate.set()
        try:
            done, _pending = await asyncio.wait({commit}, timeout=0.25)
            assert commit in done, "external-winner publication exceeded its deadline"
            with pytest.raises(ConversationCompactionCaptureDeadlineExpired) as failure:
                commit.result()
            assert failure.value.phase == "winner adoption"
            assert failure.value.generation_fatal is True
            assert registry._prepared_compaction_captures == {}  # noqa: SLF001
            assert registry._reserved_callback_generations == set()  # noqa: SLF001
            assert not registry._lifecycle_mutation_gates[  # noqa: SLF001
                "employee-a"
            ].locked()
            await asyncio.sleep(0)
            assert [child.alive for child in factory.children] == [
                False,
                False,
                False,
            ]
        finally:
            release_sink.set()
            with contextlib.suppress(BaseException):
                await commit
            await publication
        assert external_winner is not None
        assert await repository.resolve("employee-a") == external_winner
        recovered = await registry.attach(_employee())
        assert recovered.binding == external_winner
        assert recovered.child_generation == 4
        assert factory.children[3].load_requests[0].session_id == (external_winner.acp_session_id)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_rejects_non_successor_external_winner_without_adoption() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()

        async def invalid_winner_cas(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            winner = candidate.model_copy(
                update={
                    "acp_session_id": "skipped-generation-winner",
                    "binding_generation": candidate.binding_generation + 1,
                }
            )
            await repository.seed(winner)
            return winner

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=invalid_winner_cas,
        )
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            "employee-a", original.binding.binding_generation
        )
        deadline = asyncio.get_running_loop().time() + 1
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-invalid-external-winner",
            deadline,
        )

        with pytest.raises(
            ConversationCompactionCaptureFailed,
            match="external compaction winner is not the exact successor binding",
        ) as raised:
            await registry.commit_compaction_capture(prepared, deadline)

        assert raised.value.generation_fatal is True
        assert len(factory.children) == 2
        await asyncio.sleep(0)
        assert [child.alive for child in factory.children] == [False, False]
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_candidate_load_failure_invalidates_source_without_restore() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        ordinary: list[object] = []

        async def ingress(item: object) -> None:
            ordinary.append(item)

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=ingress)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            "employee-a", original.binding.binding_generation
        )
        factory.fail_private_load_session_ids.add(f"{original.binding.acp_session_id}-fork-1")

        with pytest.raises(
            ConversationCompactionCaptureFailed,
            match="private fork load: RuntimeError: scripted private load failure",
        ) as failure:
            await registry.prepare_compaction_capture(
                await registry.acquire_runtime_lease(handle),
                "capture-private-failure",
                asyncio.get_running_loop().time() + 1,
            )

        assert failure.value.generation_fatal is True
        source_child, candidate_child = factory.children
        assert source_child.private_load_requests == []
        assert [request.session_id for request in candidate_child.private_load_requests] == [
            f"{original.binding.acp_session_id}-fork-1"
        ]
        assert ordinary == []
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        recovered = await registry.attach(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == original.child_generation + 2
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["missing-capability", "hung-fork"])
def test_compaction_prepare_failure_releases_lifecycle_mutation_gate(
    failure: str,
) -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        child = factory.children[0]
        if failure == "missing-capability":
            child.supports_fork = False
            deadline = asyncio.get_running_loop().time() + 1
            capture_timeout_seconds = 1.0
            expected_exception: type[BaseException] = ConversationCompactionCaptureFailed
            expected_reason = "failed during fork.*does not advertise session/fork"
        else:
            child.fork_gate = asyncio.Event()
            deadline = asyncio.get_running_loop().time() + 0.02
            capture_timeout_seconds = 0.02
            expected_exception = ConversationCompactionCaptureDeadlineExpired
            expected_reason = (
                "configured 0.02-second budget during fork; durable binding stayed on generation 1"
            )

        with pytest.raises(
            expected_exception,
            match=expected_reason,
        ) as failure_result:
            await registry.prepare_compaction_capture(
                await registry.acquire_runtime_lease(handle),
                f"capture-{failure}",
                deadline,
                capture_timeout_seconds=capture_timeout_seconds,
            )

        assert child.private_load_requests == []
        assert await repository.resolve("employee-a") == original.binding
        assert failure_result.value.generation_fatal is True
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        await asyncio.sleep(0)
        assert child.alive is False
        replacement = await asyncio.wait_for(registry.attach(_employee()), timeout=1)
        assert replacement.binding == original.binding
        assert replacement.child_generation == original.child_generation + 1
        assert factory.children[1].load_requests[0].session_id == (original.binding.acp_session_id)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_abort_after_deadline_synchronously_clears_both_children() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 0.06
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-hung-restore",
            deadline,
            capture_timeout_seconds=0.06,
        )
        await asyncio.sleep(0.07)
        await registry.abort_compaction_capture(prepared, deadline)

        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        await asyncio.sleep(0)
        assert factory.children[0].alive is False
        assert factory.children[1].alive is False
        replacement = await asyncio.wait_for(registry.get_or_spawn(_employee()), timeout=1)
        assert replacement.child_generation == original.child_generation + 2
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_abort_after_deadline_fails_closed_while_gate_is_held() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def ingress(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=ingress)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 0.05
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-held-abort",
            deadline,
            capture_timeout_seconds=0.05,
        )
        publication = asyncio.create_task(
            factory.children[0].update_ingress(
                SessionNotification(
                    session_id=original.binding.acp_session_id,
                    update=AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        content=TextContentBlock(type="text", text="held N"),
                    ),
                )
            )
        )
        await sink_entered.wait()
        await asyncio.sleep(max(0.0, deadline - asyncio.get_running_loop().time()) + 0.01)
        abort = asyncio.create_task(registry.abort_compaction_capture(prepared, deadline))
        try:
            done, _pending = await asyncio.wait({abort}, timeout=0.15)
            assert abort in done, "abort re-waited on the held publication gate"
            abort.result()
            assert registry._prepared_compaction_captures == {}  # noqa: SLF001
            assert registry._reserved_callback_generations == set()  # noqa: SLF001
            assert not registry._lifecycle_mutation_gates[  # noqa: SLF001
                "employee-a"
            ].locked()
            with pytest.raises(ConversationRuntimeUnavailable):
                await registry.resolve_runtime_handle("employee-a", 1)
            await asyncio.sleep(0)
            assert [child.alive for child in factory.children] == [False, False]
        finally:
            release_sink.set()
            with contextlib.suppress(BaseException):
                await abort
            await publication
        assert await repository.resolve("employee-a") == original.binding
        recovered = await registry.attach(_employee())
        assert recovered.binding == original.binding
        assert recovered.child_generation == 3
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_private_fork_load_expiry_names_phase_and_preserves_binding() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        factory.private_load_gate = asyncio.Event()
        deadline = asyncio.get_running_loop().time() + 0.02
        expected_reason = (
            "Conversation compaction capture exceeded its configured 0.02-second budget "
            "during private fork load; durable binding stayed on generation 1; the "
            "uncertain runtime child was invalidated; fresh attach will resolve and load "
            "the authoritative binding"
        )

        with pytest.raises(
            ConversationCompactionCaptureDeadlineExpired,
            match=expected_reason,
        ) as failure:
            await registry.prepare_compaction_capture(
                await registry.acquire_runtime_lease(handle),
                "capture-private-load-timeout",
                deadline,
                capture_timeout_seconds=0.02,
            )

        assert failure.value.phase == "private fork load"
        assert failure.value.durable_binding_disposition == "stayed"
        assert failure.value.generation_fatal is True
        assert await repository.resolve("employee-a") == original.binding
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        recovered = await asyncio.wait_for(registry.get_or_spawn(_employee()), timeout=1)
        assert recovered.binding == original.binding
        assert recovered.child_generation == original.child_generation + 2
        assert factory.children[2].load_requests[0].session_id == (original.binding.acp_session_id)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_backend_timeout_is_immediate_without_same_child_restore() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        factory.private_load_errors_by_session[f"{original.binding.acp_session_id}-fork-1"] = (
            TimeoutError("backend private load timed out")
        )
        deadline = asyncio.get_running_loop().time() + 300
        expected_reason = (
            "Conversation compaction capture failed during private fork load: "
            "TimeoutError: backend private load timed out"
        )

        with pytest.raises(
            ConversationCompactionCaptureFailed,
            match=expected_reason,
        ) as failure:
            await registry.prepare_compaction_capture(
                await registry.acquire_runtime_lease(handle),
                "capture-private-timeout-restore-failure",
                deadline,
            )

        assert failure.value.generation_fatal is True
        assert "exceeded its configured" not in str(failure.value)
        assert await repository.resolve("employee-a") == original.binding
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle("employee-a", 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_attach_and_new_conversation_wait_for_compaction_reservation() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        first_deadline = asyncio.get_running_loop().time() + 1
        first = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-block-attach",
            first_deadline,
        )
        attach_task = asyncio.create_task(registry.attach(_employee()))
        await asyncio.sleep(0)
        assert not attach_task.done()
        assert factory.children[0].load_requests == []
        assert factory.children[0].private_load_requests == []
        assert len(factory.children[1].private_load_requests) == 1
        await registry.abort_compaction_capture(first, first_deadline)
        attached = await asyncio.wait_for(attach_task, timeout=1)
        assert attached.binding == original.binding
        assert attached.child_generation == 3
        assert factory.children[2].load_requests[0].session_id == (original.binding.acp_session_id)

        handle = await registry.resolve_runtime_handle("employee-a", 1)
        second_deadline = asyncio.get_running_loop().time() + 1
        second = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-block-new",
            second_deadline,
        )
        new_task = asyncio.create_task(registry.new_conversation(_employee()))
        await asyncio.sleep(0)
        assert not new_task.done()
        assert len(factory.children[2].new_requests) == 0
        await registry.abort_compaction_capture(second, second_deadline)
        replacement = await asyncio.wait_for(new_task, timeout=1)
        assert replacement.binding.binding_generation == 2
        assert replacement.child_generation == 5
        assert len(factory.children[4].new_requests) == 1
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_crash_respawns_and_loads_same_durable_binding_without_drift() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        binding = ConversationSessionBinding(
            employee_id="employee-a",
            acp_session_id="durable-session",
            backend_key="alpha",
            binding_generation=4,
        )
        await repository.seed(binding)
        deaths: list[tuple[str, int, BaseException | None]] = []

        async def observe_death(
            employee: ConversationEmployee,
            generation: int,
            cause: BaseException | None,
        ) -> None:
            deaths.append((employee.employee_id, generation, cause))

        catalog = EmployeeBackendCatalog(
            (static_employee_backend_registration(definition, factory),)
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=catalog,
            materialized_backends=catalog.materialize(
                EmployeeBackendBuildContext(data_directory=Path.cwd())
            ),
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            conversation_ingress=_discard_registry_ingress,
            permission_callback=_permission,
            conversation_child_death_callback=observe_death,
        )
        first = await registry.attach(_employee())
        assert first.binding == binding
        assert factory.children[0].load_requests[0].session_id == "durable-session"
        await factory.children[0].crash()
        assert deaths[0][0:2] == ("employee-a", first.child_generation)
        assert isinstance(deaths[0][2], RuntimeError)
        second = await registry.get_or_spawn(_employee())
        assert second.binding == binding
        assert second.child_generation == first.child_generation + 1
        assert factory.children[1].load_requests[0].session_id == "durable-session"
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


async def _discard_registry_ingress(item: object) -> None:
    del item


def test_concurrent_new_conversation_requests_share_one_persisted_replacement() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        first, second = await asyncio.gather(
            registry.new_conversation(_employee()),
            registry.new_conversation(_employee()),
        )
        assert first is second
        assert first.binding.binding_generation == original.binding.binding_generation + 1
        assert len(factory.children[0].new_requests) == 2
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_backend_change_never_loads_the_other_backends_session() -> None:
    async def exercise() -> None:
        alpha, beta = _definition("alpha"), _definition("beta")
        alpha_factory, beta_factory = _FakeFactory(alpha), _FakeFactory(beta)
        repository = InMemoryAcpBindingRepository()
        await repository.seed(
            ConversationSessionBinding(
                employee_id="employee-a",
                acp_session_id="alpha-session",
                backend_key="alpha",
                binding_generation=7,
            )
        )
        registry = _registry(
            {"alpha": alpha, "beta": beta},
            {"alpha": alpha_factory, "beta": beta_factory},
            repository,
        )
        record = await registry.get_or_spawn(_employee(backend_key="beta"))
        assert record.binding.backend_key == "beta"
        assert record.binding.binding_generation == 8
        assert beta_factory.children[0].load_requests == []
        assert len(beta_factory.children[0].new_requests) == 1
        assert alpha_factory.children == []
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_old_generation_sink_must_finish_before_replacement_publication() -> None:
    async def exercise() -> None:
        alpha, beta = _definition("alpha"), _definition("beta")
        alpha_factory, beta_factory = _FakeFactory(alpha), _FakeFactory(beta)
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()
        replacement_persisted = asyncio.Event()

        async def compare_and_swap(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            winner = await repository.compare_and_swap(expected, candidate)
            if candidate.backend_key == "beta":
                replacement_persisted.set()
            return winner

        async def blocked_sink(payload: object) -> None:
            del payload
            sink_entered.set()
            await release_sink.wait()

        registry = _registry(
            {"alpha": alpha, "beta": beta},
            {"alpha": alpha_factory, "beta": beta_factory},
            repository,
            ingress=blocked_sink,
            compare_and_swap=compare_and_swap,
        )
        first = await registry.get_or_spawn(_employee())
        old_update = asyncio.create_task(alpha_factory.children[0].update_ingress(object()))
        await sink_entered.wait()
        replacement = asyncio.create_task(registry.get_or_spawn(_employee(backend_key="beta")))
        await replacement_persisted.wait()
        assert not replacement.done()
        release_sink.set()
        await old_update
        second = await replacement
        assert second.child_generation > first.child_generation
        with pytest.raises(Exception, match="stale ACP update"):
            await alpha_factory.children[0].update_ingress(object())
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_stalled_durable_read_for_one_employee_does_not_hold_global_lock() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        stalled = asyncio.Event()
        release = asyncio.Event()
        calls: dict[str, int] = {}

        async def resolve(employee_id: str) -> ConversationSessionBinding | None:
            calls[employee_id] = calls.get(employee_id, 0) + 1
            if employee_id == "employee-a" and calls[employee_id] == 2:
                stalled.set()
                await release.wait()
            return await repository.resolve(employee_id)

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, resolve=resolve)
        first_task = asyncio.create_task(registry.get_or_spawn(_employee("employee-a")))
        await stalled.wait()
        second = await asyncio.wait_for(registry.get_or_spawn(_employee("employee-b")), timeout=1)
        assert second.employee.employee_id == "employee-b"
        release.set()
        await first_task
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_wrong_employee_binding_fails_closed_before_spawn() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()

        async def wrong_resolver(employee_id: str) -> ConversationSessionBinding:
            del employee_id
            return ConversationSessionBinding(
                employee_id="someone-else",
                acp_session_id="wrong",
                backend_key="alpha",
                binding_generation=1,
            )

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            resolve=wrong_resolver,
        )
        with pytest.raises(AcpEmployeeBindingError, match="another employee"):
            await registry.get_or_spawn(_employee())
        assert factory.children == []
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_failed_load_never_remints_and_next_demand_loads_same_binding_fresh() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        factory.fail_load = True
        repository = InMemoryAcpBindingRepository()
        durable = ConversationSessionBinding(
            employee_id="employee-a",
            acp_session_id="durable-load",
            backend_key="alpha",
            binding_generation=3,
        )
        await repository.seed(durable)
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        with pytest.raises(RuntimeError, match="scripted load failure"):
            await registry.get_or_spawn(_employee())
        assert factory.children[0].alive is False
        assert factory.children[0].new_requests == []
        assert await repository.resolve("employee-a") == durable
        factory.fail_load = False
        recovered = await registry.get_or_spawn(_employee())
        assert recovered.binding == durable
        assert recovered.child_generation == 2
        assert factory.children[1].load_requests[0].session_id == "durable-load"
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_cas_exception_retires_child_and_recovers_old_binding() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()

        async def compare_and_swap(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is not None:
                raise RuntimeError("persistence unavailable")
            return await repository.compare_and_swap(expected, candidate)

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=compare_and_swap,
        )
        original = await registry.get_or_spawn(_employee())
        with pytest.raises(RuntimeError, match="persistence unavailable"):
            await registry.new_conversation(_employee())
        assert original.child.alive is False
        assert await repository.resolve("employee-a") == original.binding
        recovered = await registry.get_or_spawn(_employee())
        assert recovered.child_generation == original.child_generation + 1
        assert recovered.binding == original.binding
        assert factory.children[1].load_requests[0].session_id == original.binding.acp_session_id
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_cas_loser_is_closed_and_winner_loaded_by_fresh_child() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        external_winner: ConversationSessionBinding | None = None

        async def compare_and_swap(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            nonlocal external_winner
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            external_winner = ConversationSessionBinding(
                employee_id=candidate.employee_id,
                acp_session_id="external-winner",
                backend_key="alpha",
                binding_generation=expected.binding_generation + 1,
            )
            await repository.seed(external_winner)
            return external_winner

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            compare_and_swap=compare_and_swap,
        )
        original = await registry.get_or_spawn(_employee())
        adopted = await registry.new_conversation(_employee())
        assert external_winner is not None
        assert adopted.binding == external_winner
        assert original.child.alive is False
        assert adopted.child_generation == original.child_generation + 1
        assert factory.children[1].private_load_requests[0].session_id == ("external-winner")
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_post_cas_reread_failure_retires_persisted_candidate_child() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        resolve_calls = 0
        fail_next_publication_read = False

        async def resolve(employee_id: str) -> ConversationSessionBinding | None:
            nonlocal resolve_calls, fail_next_publication_read
            resolve_calls += 1
            if fail_next_publication_read:
                fail_next_publication_read = False
                raise RuntimeError("durable reread failed")
            return await repository.resolve(employee_id)

        registry = _registry(
            {"alpha": definition},
            {"alpha": factory},
            repository,
            resolve=resolve,
        )
        original = await registry.get_or_spawn(_employee())
        fail_next_publication_read = True
        with pytest.raises(RuntimeError, match="durable reread failed"):
            await registry.new_conversation(_employee())
        assert original.child.alive is False
        persisted_candidate = await repository.resolve("employee-a")
        assert persisted_candidate is not None
        assert persisted_candidate.binding_generation == 2
        recovered = await registry.get_or_spawn(_employee())
        assert recovered.binding == persisted_candidate
        assert factory.children[1].load_requests[0].session_id == persisted_candidate.acp_session_id
        assert resolve_calls >= 3
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_late_old_generation_death_cannot_retire_replacement() -> None:
    async def exercise() -> None:
        alpha, beta = _definition("alpha"), _definition("beta")
        alpha_factory, beta_factory = _FakeFactory(alpha), _FakeFactory(beta)
        repository = InMemoryAcpBindingRepository()
        registry = _registry(
            {"alpha": alpha, "beta": beta},
            {"alpha": alpha_factory, "beta": beta_factory},
            repository,
        )
        old = await registry.get_or_spawn(_employee())
        replacement = await registry.get_or_spawn(_employee(backend_key="beta"))
        await alpha_factory.children[0].death_callback(RuntimeError("late old death"))
        still_current = await registry.get_or_spawn(_employee(backend_key="beta"))
        assert still_current is replacement
        assert still_current.child_generation > old.child_generation
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_incompatible_get_attach_and_new_waves_re_evaluate_requested_backend() -> None:
    async def exercise_get() -> None:
        alpha, beta = _definition("alpha"), _definition("beta")
        alpha_factory, beta_factory = _FakeFactory(alpha), _FakeFactory(beta)
        alpha_factory.creation_gate = asyncio.Event()
        repository = InMemoryAcpBindingRepository()
        registry = _registry(
            {"alpha": alpha, "beta": beta},
            {"alpha": alpha_factory, "beta": beta_factory},
            repository,
        )
        alpha_task = asyncio.create_task(registry.get_or_spawn(_employee()))
        await alpha_factory.creation_started.wait()
        beta_task = asyncio.create_task(registry.get_or_spawn(_employee(backend_key="beta")))
        alpha_factory.creation_gate.set()
        alpha_record, beta_record = await asyncio.gather(alpha_task, beta_task)
        assert alpha_record.binding.backend_key == "alpha"
        assert beta_record.binding.backend_key == "beta"
        assert beta_factory.children
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    async def exercise_attach() -> None:
        alpha, beta = _definition("alpha"), _definition("beta")
        alpha_factory, beta_factory = _FakeFactory(alpha), _FakeFactory(beta)
        repository = InMemoryAcpBindingRepository()
        registry = _registry(
            {"alpha": alpha, "beta": beta},
            {"alpha": alpha_factory, "beta": beta_factory},
            repository,
        )
        await registry.get_or_spawn(_employee())
        load_gate = asyncio.Event()
        alpha_factory.children[0].private_load_gate = load_gate
        alpha_task = asyncio.create_task(registry.attach(_employee()))
        await alpha_factory.children[0].private_load_started.wait()
        beta_task = asyncio.create_task(registry.attach(_employee(backend_key="beta")))
        load_gate.set()
        alpha_record, beta_record = await asyncio.gather(alpha_task, beta_task)
        assert alpha_record.binding.backend_key == "alpha"
        assert beta_record.binding.backend_key == "beta"
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    async def exercise_new() -> None:
        alpha, beta = _definition("alpha"), _definition("beta")
        alpha_factory, beta_factory = _FakeFactory(alpha), _FakeFactory(beta)
        repository = InMemoryAcpBindingRepository()
        registry = _registry(
            {"alpha": alpha, "beta": beta},
            {"alpha": alpha_factory, "beta": beta_factory},
            repository,
        )
        await registry.get_or_spawn(_employee())
        new_gate = asyncio.Event()
        alpha_factory.children[0].new_session_gate = new_gate
        alpha_factory.children[0].new_session_started.clear()
        alpha_task = asyncio.create_task(registry.new_conversation(_employee()))
        await alpha_factory.children[0].new_session_started.wait()
        beta_task = asyncio.create_task(registry.new_conversation(_employee(backend_key="beta")))
        new_gate.set()
        alpha_record, beta_record = await asyncio.gather(alpha_task, beta_task)
        assert alpha_record.binding.backend_key == "alpha"
        assert beta_record.binding.backend_key == "beta"
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise_get())
    asyncio.run(exercise_attach())
    asyncio.run(exercise_new())


def test_shutdown_settles_prepared_compaction_source_candidate_and_reservation() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        original = await registry.get_or_spawn(_employee())
        handle = await registry.resolve_runtime_handle(
            "employee-a", original.binding.binding_generation
        )
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(handle),
            "capture-shutdown",
            asyncio.get_running_loop().time() + 1,
        )
        state = registry._prepared_compaction_captures[  # noqa: SLF001
            prepared.transaction_identity
        ]
        assert state.lifecycle_mutation_gate.locked()
        assert len(factory.children) == 2

        await registry.shutdown(asyncio.get_running_loop().time() + 1)

        assert [child.alive for child in factory.children] == [False, False]
        assert registry._prepared_compaction_captures == {}  # noqa: SLF001
        assert registry._reserved_callback_generations == set()  # noqa: SLF001
        assert not state.lifecycle_mutation_gate.locked()

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["factory", "initialize", "load"])
def test_shutdown_cancels_partial_factory_initialize_and_load(stage: str) -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _FakeFactory(definition)
        repository = InMemoryAcpBindingRepository()
        gate = asyncio.Event()
        if stage == "factory":
            factory.creation_gate = gate
        elif stage == "initialize":
            factory.initialize_gate = gate
        else:
            factory.load_gate = gate
            await repository.seed(
                ConversationSessionBinding(
                    employee_id="employee-a",
                    acp_session_id="partial-load",
                    backend_key="alpha",
                    binding_generation=1,
                )
            )
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        demand = asyncio.create_task(registry.get_or_spawn(_employee()))
        if stage == "factory":
            await factory.creation_started.wait()
        else:
            while not factory.children:
                next_turn = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(next_turn.set_result, None)
                await next_turn
            started = (
                factory.children[0].initialize_started
                if stage == "initialize"
                else factory.children[0].load_started
            )
            await started.wait()
        await registry.shutdown(asyncio.get_running_loop().time() + 1)
        assert demand.cancelled()
        if factory.children:
            assert factory.children[0].alive is False

    asyncio.run(exercise())


def test_shutdown_deadline_is_hard_for_cancellation_resistant_factory() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _CancellationResistantFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        demand = asyncio.create_task(registry.get_or_spawn(_employee()))
        await factory.creation_started.wait()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.04
        with pytest.raises(AcpEmployeeRegistryShutdownError) as raised:
            await registry.shutdown(deadline)
        assert loop.time() < deadline + 0.04
        assert raised.value.unfinished_employee_ids == ("employee-a",)
        factory.release.set()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(demand, timeout=1)

    asyncio.run(exercise())


def test_all_child_shutdown_uses_one_deadline_and_forces_concurrently() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _CancellationResistantCloseFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _registry({"alpha": definition}, {"alpha": factory}, repository)
        await asyncio.gather(
            registry.get_or_spawn(_employee("employee-a")),
            registry.get_or_spawn(_employee("employee-b")),
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.05
        with pytest.raises(AcpEmployeeRegistryShutdownError) as raised:
            await registry.shutdown(deadline)
        assert loop.time() < deadline + 0.04
        assert raised.value.unfinished_employee_ids == ("employee-a", "employee-b")
        children = [
            child
            for child in factory.children
            if isinstance(child, _CancellationResistantCloseChild)
        ]
        assert [child.force_close_calls for child in children] == [1, 1]
        for child in children:
            child.close_release.set()

    asyncio.run(exercise())


def test_shutdown_blocked_generation_sink_returns_by_shared_deadline() -> None:
    async def exercise() -> None:
        definition = _definition("alpha")
        factory = _CancellationResistantCloseFactory(definition)
        repository = InMemoryAcpBindingRepository()
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def sink(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        registry = _registry({"alpha": definition}, {"alpha": factory}, repository, ingress=sink)
        await registry.get_or_spawn(_employee())
        update = asyncio.create_task(factory.children[0].update_ingress(object()))
        await sink_entered.wait()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.04
        with pytest.raises(AcpEmployeeRegistryShutdownError) as raised:
            await registry.shutdown(deadline)
        assert loop.time() < deadline + 0.04
        assert raised.value.unfinished_employee_ids == ("employee-a",)
        release_sink.set()
        await update
        child = factory.children[0]
        assert isinstance(child, _CancellationResistantCloseChild)
        child.close_release.set()

    asyncio.run(exercise())


def test_production_runtime_passes_acp01_conformance_probes_and_mutations_fail() -> None:
    subject = asyncio.run(ProductionAcp01ConformanceSubject.create())
    load = subject.observe_load_replay()
    refresh = subject.observe_refresh_binding()
    order = subject.observe_callback_order()
    assert_load_replay(load)
    assert_durable_refresh(refresh)
    assert_callback_wire_order(order)
    with pytest.raises(AssertionError):
        assert_load_replay(mutate_probe_evidence("load_replay_before_response", load))  # type: ignore[arg-type]
    with pytest.raises(AssertionError):
        assert_durable_refresh(mutate_probe_evidence("durable_refresh", refresh))  # type: ignore[arg-type]
    with pytest.raises(AssertionError):
        assert_callback_wire_order(mutate_probe_evidence("callback_wire_order", order))  # type: ignore[arg-type]
