from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from acp.schema import (
    AgentCapabilities,
    CancelNotification,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
)
from fastapi.testclient import TestClient

from planner.conversation.backend_catalog import (
    EmployeeBackendBuildContext,
    EmployeeBackendCatalog,
    EmployeeBackendRegistration,
    MaterializedEmployeeBackendRegistration,
    static_employee_backend_registration,
)
from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.composition import (
    ConversationComposition,
    ConversationTestOptions,
    _BindOnceAsyncCallback,
)
from planner.conversation.configuration import ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES
from planner.conversation.wire_contracts import CancelAction
from planner.core import loops as loops_module
from planner.core.clock import TestClock as MutableTestClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime.automatic_employee_step_eligibility_wake import (
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.tickets import data as tickets_data
from planner.worker_context.service import SqliteWorkerContextService
from planner.worker_types.configuration import (
    ConfiguredEmployeeRuntimeDefinitions,
    build_employee_runtime_definitions,
)


class _Strategy:
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


def _definition() -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key="hermes",
        argv=("/scripted/hermes", "acp"),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name="scripted-hermes",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=True,
            observes_compaction=True,
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False,
            terminal=False,
            permission=True,
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_Strategy(),
    )


class _Child:
    def __init__(
        self,
        generation: int,
        definition: AgentBackendDefinition,
    ) -> None:
        self.generation = generation
        self.definition = definition
        self.alive = True

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        assert request.client_info.name == "panels"
        return InitializeResponse(
            protocol_version=request.protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name=self.definition.expected_agent_name,
                version=self.definition.expected_agent_version,
            ),
        )

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse:
        del request
        return NewSessionResponse(session_id=f"session-{self.generation}")

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        del request
        return LoadSessionResponse()

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        del request
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        self.alive = False


class _Factory:
    def __init__(self, definition: AgentBackendDefinition) -> None:
        self.definition = definition
        self.children: list[_Child] = []

    async def create(
        self,
        employee: Any,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _Child:
        del employee, update_ingress, permission_callback, death_callback
        child = _Child(generation, self.definition)
        self.children.append(child)
        return child


def _runtime_definitions(
    definition: AgentBackendDefinition,
    factory: Any,
) -> ConfiguredEmployeeRuntimeDefinitions:
    catalog = EmployeeBackendCatalog((static_employee_backend_registration(definition, factory),))
    return build_employee_runtime_definitions(catalog)


def _database(tmp_path: Path) -> tuple[str, MutableTestClock, str]:
    db_path = str(tmp_path / "planning.db")
    test_clock = MutableTestClock(datetime.fromtimestamp(1, tz=UTC))
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="ACP composition",
        actor="test",
        now=1,
        title_max_chars=200,
    )
    conn.close()
    return db_path, test_clock, ticket.id


def test_bind_once_callbacks_fail_closed_until_exactly_one_binding() -> None:
    async def exercise() -> None:
        slot = _BindOnceAsyncCallback[[str], str]("test")
        with pytest.raises(RuntimeError, match="not bound"):
            await slot("before")

        async def callback(value: str) -> str:
            return value + "-bound"

        slot.bind(callback)
        assert await slot("after") == "after-bound"
        with pytest.raises(RuntimeError, match="already bound"):
            slot.bind(callback)

    asyncio.run(exercise())


def test_production_browser_capacity_is_1024_and_test_options_can_override(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, clock, _ticket_id = _database(tmp_path)
        production = ConversationComposition.build(
            db_path=db_path,
            busy_timeout_ms=5000,
            clock=clock,
            repository_root=Path.cwd(),
            loop=asyncio.get_running_loop(),
            test_options=None,
        )
        assert ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES == 1_024
        assert production.hub._browser_capacity == 1_024  # noqa: SLF001
        await production.shutdown(asyncio.get_running_loop().time() + 1)

        override_db = str(tmp_path / "override.db")
        definition = _definition()
        override = ConversationComposition.build(
            db_path=override_db,
            busy_timeout_ms=5000,
            clock=clock,
            repository_root=tmp_path,
            loop=asyncio.get_running_loop(),
            test_options=ConversationTestOptions(
                employee_runtime_definitions=_runtime_definitions(
                    definition, _Factory(definition)
                ),
                browser_capacity=3,
            ),
        )
        assert override.hub._browser_capacity == 3  # noqa: SLF001
        await override.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_single_conversation_composition_owns_runtime_and_closes_browser_admission(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, clock, ticket_id = _database(tmp_path)
        definition = _definition()
        factory = _Factory(definition)
        composition = ConversationComposition.build(
            db_path=db_path,
            busy_timeout_ms=5000,
            clock=clock,
            repository_root=tmp_path,
            loop=asyncio.get_running_loop(),
            test_options=ConversationTestOptions(
                employee_runtime_definitions=_runtime_definitions(definition, factory),
                connection_id_factory=lambda: "browser-one",
                worker_client_message_id_factory=lambda: "worker-one",
                permission_request_id_factory=lambda: "permission-one",
            ),
        )
        assert composition.hub is composition.permission_broker._publisher
        assert composition.registry is composition.broker._runtime
        assert composition.hub is composition.broker._publisher
        assert composition.hub is composition.broker._requested_cancel_transition_port
        assert composition.step_gateway.status().available is True

        subscription = await composition.hub.attach_browser(ticket_id)
        assert subscription.closed.is_set() is False
        await composition.close_admission()
        assert composition.step_gateway.status().available is False
        assert subscription.closed.is_set() is True
        with pytest.raises(RuntimeError, match="closing"):
            await composition.hub.attach_browser(ticket_id, connection_id="browser-two")
        with pytest.raises(RuntimeError, match="closing"):
            await composition.hub.dispatch_action(
                subscription.connection_id,
                CancelAction(type="cancel", employee_id=ticket_id),
            )
        await composition.shutdown(asyncio.get_running_loop().time() + 1)
        assert factory.children and all(not child.alive for child in factory.children)

    asyncio.run(exercise())


def test_conversation_composition_injects_sqlite_worker_context_into_step_gateway(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, clock, _ticket_id = _database(tmp_path)
        definition = _definition()
        composition = ConversationComposition.build(
            db_path=db_path,
            busy_timeout_ms=3210,
            clock=clock,
            repository_root=tmp_path,
            loop=asyncio.get_running_loop(),
            test_options=ConversationTestOptions(
                employee_runtime_definitions=_runtime_definitions(definition, _Factory(definition)),
            ),
        )

        assert isinstance(
            composition.step_gateway._worker_context_service,  # noqa: SLF001
            SqliteWorkerContextService,
        )
        await composition.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_employee_backend_preflights_run_once_in_catalog_order_without_registry_spawn(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, clock, _ticket_id = _database(tmp_path)
        order: list[str] = []
        contexts: list[EmployeeBackendBuildContext] = []
        hermes_definition = _definition()
        probe_definition = replace(hermes_definition, backend_key="probe-backend")
        hermes_factory = _Factory(hermes_definition)
        probe_factory = _Factory(probe_definition)

        def registration(
            definition: AgentBackendDefinition,
            factory: _Factory,
        ) -> EmployeeBackendRegistration:
            async def preflight() -> None:
                order.append(definition.backend_key)

            def materialize(
                context: EmployeeBackendBuildContext,
            ) -> MaterializedEmployeeBackendRegistration:
                contexts.append(context)
                return MaterializedEmployeeBackendRegistration(
                    definition=definition,
                    child_factory=factory,
                    is_executable=lambda: True,
                    startup_preflight=preflight,
                )

            return EmployeeBackendRegistration(definition.backend_key, materialize)

        catalog = EmployeeBackendCatalog(
            (
                registration(hermes_definition, hermes_factory),
                registration(probe_definition, probe_factory),
            )
        )
        composition = ConversationComposition.build(
            db_path=db_path,
            busy_timeout_ms=5000,
            clock=clock,
            repository_root=tmp_path,
            loop=asyncio.get_running_loop(),
            test_options=ConversationTestOptions(
                employee_runtime_definitions=build_employee_runtime_definitions(catalog),
            ),
        )

        assert [context.repository_root for context in contexts] == [
            tmp_path.resolve(),
            tmp_path.resolve(),
        ]
        assert hermes_factory.children == []
        assert probe_factory.children == []
        await composition.run_employee_backend_startup_preflights()
        await composition.run_employee_backend_startup_preflights()
        assert order == ["hermes", "probe-backend"]
        assert hermes_factory.children == []
        assert probe_factory.children == []
        await composition.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_production_uses_only_conversation_step_gateway_and_one_shutdown_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, clock, _ticket_id = _database(tmp_path)
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCH_ENABLED": "0",
        },
    )
    order: list[tuple[str, float | None]] = []
    step_gateway = object()

    class _FakeHub:
        async def websocket(self, websocket: Any) -> None:
            del websocket

    class _FakeComposition:
        def __init__(self) -> None:
            self.hub = _FakeHub()
            self.step_gateway = step_gateway

        async def close_admission(self) -> None:
            order.append(("conversation.close_admission", None))

        async def run_employee_backend_startup_preflights(self) -> None:
            order.append(("conversation.preflight", None))

        async def shutdown(self, deadline: float) -> None:
            order.append(("conversation.shutdown", deadline))

    composition = _FakeComposition()

    class _Runtime:
        employee_step_runner = object()
        automatic_employee_step_eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()

        async def stop(self, *, deadline: float | None = None) -> None:
            order.append(("runtime.stop", deadline))

    def build_composition(cls: object, **kwargs: Any) -> _FakeComposition:
        del cls, kwargs
        order.append(("conversation.build", None))
        return composition

    def start_runtime(
        runtime_config: Any,
        runtime_clock: Any,
        *,
        step_gateway: object,
        **kwargs: Any,
    ) -> _Runtime:
        del runtime_config, runtime_clock
        assert kwargs == {}
        assert step_gateway is composition.step_gateway
        order.append(("runtime.start", None))
        return _Runtime()

    original_audit = tickets_data.audit_ticket_registry_integrity

    def audit_ticket_registry_integrity(conn: Any) -> None:
        order.append(("storage.audit", None))
        original_audit(conn)

    monkeypatch.setattr(
        ConversationComposition,
        "build",
        classmethod(build_composition),
    )
    monkeypatch.setattr(
        tickets_data,
        "audit_ticket_registry_integrity",
        audit_ticket_registry_integrity,
    )
    monkeypatch.setattr(loops_module, "start_background_loops", start_runtime)
    app = create_app(
        config,
        clock,
        lambda: connect(db_path),
    )
    with TestClient(app):
        assert app.state.conversation is composition
        assert not hasattr(app.state, "shared_gateway")
        assert not hasattr(app.state, "employee_child_pool")
        assert not hasattr(app.state, "employee_child_relay")

    names = [name for name, _deadline in order]
    assert names == [
        "storage.audit",
        "conversation.build",
        "conversation.preflight",
        "runtime.start",
        "conversation.close_admission",
        "runtime.stop",
        "conversation.shutdown",
    ]
    runtime_deadline = order[5][1]
    conversation_deadline = order[6][1]
    assert runtime_deadline is not None
    assert conversation_deadline == runtime_deadline


def test_production_loop_start_failure_closes_conversation_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, clock, _ticket_id = _database(tmp_path)
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCH_ENABLED": "0",
        },
    )
    order: list[tuple[str, float | None]] = []

    class _FailedStartupComposition:
        step_gateway = object()

        async def run_employee_backend_startup_preflights(self) -> None:
            order.append(("preflight", None))

        async def close_admission(self) -> None:
            order.append(("close", None))

        async def shutdown(self, deadline: float) -> None:
            order.append(("shutdown", deadline))

    composition = _FailedStartupComposition()

    def build_composition(cls: object, **kwargs: Any) -> _FailedStartupComposition:
        del cls, kwargs
        order.append(("build", None))
        return composition

    def fail_start(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        order.append(("start", None))
        raise RuntimeError("loop startup failed")

    monkeypatch.setattr(
        ConversationComposition,
        "build",
        classmethod(build_composition),
    )
    monkeypatch.setattr(loops_module, "start_background_loops", fail_start)
    app = create_app(
        config,
        clock,
        lambda: connect(db_path),
    )
    with pytest.raises(RuntimeError, match="loop startup failed"):
        with TestClient(app):
            pass

    assert [name for name, _deadline in order] == [
        "build",
        "preflight",
        "start",
        "close",
        "shutdown",
    ]
    assert order[-1][1] is not None
    assert app.state.conversation is None


def test_production_backend_preflight_failure_closes_before_runtime_or_routes_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, clock, _ticket_id = _database(tmp_path)
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCH_ENABLED": "0",
        },
    )
    order: list[tuple[str, float | None]] = []

    class _FailedPreflightComposition:
        step_gateway = object()

        async def run_employee_backend_startup_preflights(self) -> None:
            order.append(("preflight", None))
            raise RuntimeError("backend preflight failed")

        async def close_admission(self) -> None:
            order.append(("close", None))

        async def shutdown(self, deadline: float) -> None:
            order.append(("shutdown", deadline))

    composition = _FailedPreflightComposition()

    def build_composition(cls: object, **kwargs: Any) -> _FailedPreflightComposition:
        del cls, kwargs
        order.append(("build", None))
        return composition

    def must_not_start_runtime(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("runtime started before backend preflight completed")

    monkeypatch.setattr(
        ConversationComposition,
        "build",
        classmethod(build_composition),
    )
    monkeypatch.setattr(
        loops_module,
        "start_background_loops",
        must_not_start_runtime,
    )
    app = create_app(config, clock, lambda: connect(db_path))

    with pytest.raises(RuntimeError, match="backend preflight failed"):
        with TestClient(app):
            raise AssertionError("routes were exposed after backend preflight failure")

    assert [name for name, _deadline in order] == [
        "build",
        "preflight",
        "close",
        "shutdown",
    ]
    assert order[-1][1] is not None
    assert app.state.conversation is None


def test_employee_backend_catalog_rejects_empty_duplicate_and_runtime_key_mismatch(
    tmp_path: Path,
) -> None:
    definition = _definition()
    registration = static_employee_backend_registration(definition, _Factory(definition))
    with pytest.raises(ValueError, match="must not be empty"):
        EmployeeBackendCatalog(())
    with pytest.raises(ValueError, match="duplicate"):
        EmployeeBackendCatalog((registration, registration))
    mismatched = EmployeeBackendCatalog(
        (EmployeeBackendRegistration("probe-backend", registration.runtime_builder),)
    )
    with pytest.raises(ValueError, match="does not match"):
        mismatched.materialize(EmployeeBackendBuildContext(data_directory=tmp_path))


def test_conversation_test_options_are_rejected_outside_test_mode(
    tmp_path: Path,
) -> None:
    db_path, clock, _ticket_id = _database(tmp_path)
    config = load_config(
        path=None,
        env={"PLAN_DB_PATH": db_path, "PLAN_LOGS_DIR": str(tmp_path / "logs")},
    )
    definition = _definition()
    with pytest.raises(ValueError, match="only in test mode"):
        create_app(
            config,
            clock,
            lambda: connect(db_path),
            conversation_test_options=ConversationTestOptions(
                employee_runtime_definitions=_runtime_definitions(
                    definition, cast(Any, _Factory(definition))
                ),
            ),
        )
