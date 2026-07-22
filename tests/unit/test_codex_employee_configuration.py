from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentCapabilities,
    CancelNotification,
    ForkSessionRequest,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    RequestPermissionResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)
from tests.support.acp_in_memory_binding_repository import (
    InMemoryAcpBindingRepository,
)

from planner.conversation import codex_backend
from planner.conversation.backend_catalog import (
    EmployeeBackendBuildContext,
    EmployeeBackendCatalog,
)
from planner.conversation.contracts import (
    ConversationEmployee,
    ConversationSessionBinding,
)
from planner.conversation.employee_configuration import (
    EmployeeConfigurationCatalog,
    EmployeeConfigurationCatalogOption,
    EmployeeConfigurationCatalogService,
    EmployeeConfigurationError,
    StableAcpEmployeeSessionConfigurationAdapter,
)
from planner.conversation.employee_registry import AcpEmployeeRegistry
from planner.tickets.contracts import EmployeeLaunchConfiguration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NODE_EXECUTABLE = Path(shutil.which("node") or "")


def _select(
    config_id: str,
    category: str,
    current: str,
    values: tuple[tuple[str, str, str | None], ...],
) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        type="select",
        id=config_id,
        name=f"Provider {category}",
        category=category,
        current_value=current,
        options=[
            SessionConfigSelectOption(
                value=value,
                name=label,
                description=description,
            )
            for value, label, description in values
        ],
    )


def _initial_options() -> list[SessionConfigOptionSelect]:
    return [
        _select(
            "not-the-codex-model-id",
            "model",
            "codex-native",
            (
                ("codex-native", "Codex Native", "Account default"),
                ("codex-selected", "Codex Selected", "Selected exactly"),
            ),
        ),
        _select(
            "unrelated-option-id",
            "agent_mode",
            "agent",
            (("agent", "Agent", "Not employee configuration"),),
        ),
        _select(
            "not-the-codex-reasoning-id",
            "thought_level",
            "medium",
            (("medium", "Medium", "Native effort"),),
        ),
    ]


def _refreshed_options(
    *, reasoning_values: tuple[tuple[str, str, str | None], ...] | None = None
) -> list[SessionConfigOptionSelect]:
    values = reasoning_values or (
        ("high", "High", "Deep reasoning"),
        ("max", "Max", "Maximum reasoning"),
    )
    return [
        _select(
            "refreshed-model-option-id",
            "model",
            "codex-selected",
            (
                ("codex-native", "Codex Native", "Account default"),
                ("codex-selected", "Codex Selected", "Selected exactly"),
            ),
        ),
        _select(
            "refreshed-reasoning-option-id",
            "thought_level",
            values[0][0],
            values,
        ),
    ]


@dataclass
class _Scenario:
    initial_options: list[SessionConfigOptionSelect] = field(
        default_factory=_initial_options
    )
    model_response_options: list[SessionConfigOptionSelect] = field(
        default_factory=_refreshed_options
    )
    set_failures: set[tuple[str, str]] = field(default_factory=set)
    close_session_failure: bool = False
    close_failure: bool = False


@dataclass
class _ScriptedChild:
    factory: _ScriptedFactory
    generation: int
    death_callback: Any
    alive: bool = True
    supports_session_fork: bool = False

    async def initialize(self, request: object) -> InitializeResponse:
        del request
        self.factory.operations.append("initialize")
        return InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name=self.factory.definition.expected_agent_name,
                version=self.factory.definition.expected_agent_version,
            ),
        )

    async def new_session(self, request: object) -> NewSessionResponse:
        del request
        self.factory.session_number += 1
        session_id = f"codex-session-{self.generation}-{self.factory.session_number}"
        self.factory.operations.append(f"new_session:{session_id}")
        return NewSessionResponse(
            session_id=session_id,
            config_options=list(self.factory.scenario.initial_options),
        )

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        self.factory.operations.append(
            f"set_config_option:{session_id}:{config_id}:{value}"
        )
        if (config_id, value) in self.factory.scenario.set_failures:
            raise RuntimeError("scripted set failure")
        if config_id == "not-the-codex-model-id":
            return SetSessionConfigOptionResponse(
                config_options=list(self.factory.scenario.model_response_options)
            )
        return SetSessionConfigOptionResponse(
            config_options=[
                _select(
                    config_id,
                    "thought_level",
                    value,
                    ((value, value.title(), "Selected effort"),),
                )
            ]
        )

    async def set_session_mode(self, session_id: str, mode_id: str) -> None:
        self.factory.operations.append(f"set_session_mode:{session_id}:{mode_id}")

    async def close_session(self, session_id: str) -> None:
        self.factory.operations.append(f"close_session:{session_id}")
        if self.factory.scenario.close_session_failure:
            raise RuntimeError("scripted session close failure")

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        self.factory.operations.append(f"load_session:{request.session_id}")
        return LoadSessionResponse()

    async def capture_load_session(
        self, request: LoadSessionRequest, private_ingress: Any
    ) -> LoadSessionResponse:
        del private_ingress
        self.factory.operations.append(f"capture_load_session:{request.session_id}")
        return LoadSessionResponse()

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        raise RuntimeError(f"unexpected session fork for {request.session_id}")

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self.factory.operations.append(f"prompt:{request.session_id}")
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        self.factory.operations.append("close_child")
        self.alive = False
        if self.factory.scenario.close_failure:
            raise RuntimeError("scripted child close failure")


class _ScriptedFactory:
    def __init__(self, definition: Any, scenario: _Scenario) -> None:
        self.definition = definition
        self.scenario = scenario
        self.children: list[_ScriptedChild] = []
        self.operations: list[str] = []
        self.session_number = 0

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _ScriptedChild:
        del employee, update_ingress, permission_callback
        child = _ScriptedChild(self, generation, death_callback)
        self.children.append(child)
        return child


class _FactoryConstructor:
    def __init__(self, scenario: _Scenario) -> None:
        self.scenario = scenario
        self.factory: _ScriptedFactory | None = None

    def __call__(self, definition: Any) -> _ScriptedFactory:
        self.factory = _ScriptedFactory(definition, self.scenario)
        return self.factory


def _materialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: _Scenario | None = None,
) -> tuple[Any, _ScriptedFactory, EmployeeBackendCatalog]:
    constructor = _FactoryConstructor(scenario or _Scenario())
    monkeypatch.setattr(
        codex_backend,
        "SdkAcpEmployeeChildFactory",
        constructor,
    )
    registration = codex_backend.build_codex_employee_backend_registration()
    catalog = EmployeeBackendCatalog((registration,))
    materialized = catalog.materialize(
        EmployeeBackendBuildContext(
            repository_root=REPOSITORY_ROOT,
            data_directory=tmp_path,
        )
    )[0]
    assert constructor.factory is not None
    return materialized, constructor.factory, catalog


def _adapter(materialized: Any) -> StableAcpEmployeeSessionConfigurationAdapter:
    adapter = materialized.employee_configuration_adapter
    assert isinstance(adapter, StableAcpEmployeeSessionConfigurationAdapter)
    return adapter


def test_codex_registration_reuses_one_locked_definition_and_factory_for_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    materialized, factory, _catalog = _materialize(monkeypatch, tmp_path)
    adapter = _adapter(materialized)

    assert materialized.definition.backend_key == codex_backend.CODEX_BACKEND_KEY
    assert materialized.definition.argv == (
        str(NODE_EXECUTABLE.resolve()),
        str(
            REPOSITORY_ROOT
            / "agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ),
    )
    assert materialized.child_factory is factory
    assert factory.definition is materialized.definition
    assert adapter._definition is materialized.definition
    assert adapter._child_factory is factory
    assert adapter._workspace_root == REPOSITORY_ROOT


def test_codex_catalog_uses_semantic_categories_exact_values_and_server_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def exercise() -> None:
        materialized, factory, _catalog = _materialize(monkeypatch, tmp_path)
        service = EmployeeConfigurationCatalogService(
            {codex_backend.CODEX_BACKEND_KEY: _adapter(materialized)}
        )

        native = await service.catalog(codex_backend.CODEX_BACKEND_KEY, None)
        assert await service.catalog(codex_backend.CODEX_BACKEND_KEY, None) is native
        selected = await service.catalog(
            codex_backend.CODEX_BACKEND_KEY, "codex-selected"
        )
        assert (
            await service.catalog(
                codex_backend.CODEX_BACKEND_KEY, "codex-selected"
            )
            is selected
        )

        assert native == EmployeeConfigurationCatalog(
            employee_backend="codex",
            candidate_model=None,
            native_model="codex-native",
            models=(
                EmployeeConfigurationCatalogOption(
                    value="codex-native",
                    label="Codex Native",
                    description="Account default",
                ),
                EmployeeConfigurationCatalogOption(
                    value="codex-selected",
                    label="Codex Selected",
                    description="Selected exactly",
                ),
            ),
            reasoning_supported=True,
            native_reasoning_effort="medium",
            reasoning_efforts=(
                EmployeeConfigurationCatalogOption(
                    value="medium", label="Medium", description="Native effort"
                ),
            ),
        )
        assert selected.native_reasoning_effort == "high"
        assert [option.value for option in selected.reasoning_efforts] == ["high", "max"]
        assert len(factory.children) == 2
        assert factory.operations == [
            "initialize",
            "new_session:codex-session-1-1",
            "close_session:codex-session-1-1",
            "close_child",
            "initialize",
            "new_session:codex-session-1-2",
            (
                "set_config_option:codex-session-1-2:"
                "not-the-codex-model-id:codex-selected"
            ),
            "close_session:codex-session-1-2",
            "close_child",
        ]

    asyncio.run(exercise())


def test_codex_catalog_reports_reasoning_unsupported_when_category_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def exercise() -> None:
        options = _initial_options()[:2]
        materialized, _factory, _catalog = _materialize(
            monkeypatch, tmp_path, _Scenario(initial_options=options)
        )
        result = await _adapter(materialized).discover_catalog(None)

        assert result.reasoning_supported is False
        assert result.native_reasoning_effort is None
        assert result.reasoning_efforts == ()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scenario,candidate_model,error_match",
    [
        (_Scenario(), "missing-model", "does not advertise selected value"),
        (
            _Scenario(initial_options=_initial_options() + [_initial_options()[0]]),
            None,
            "exactly one select option",
        ),
        (
            _Scenario(
                initial_options=[
                    _select(
                        "bad-model",
                        "model",
                        "not-advertised",
                        (("codex-native", "Codex Native", None),),
                    )
                ]
            ),
            None,
            "current value is not advertised",
        ),
        (
            _Scenario(
                set_failures={
                    ("not-the-codex-model-id", "codex-selected")
                }
            ),
            "codex-selected",
            "catalog discovery failed",
        ),
        (_Scenario(close_session_failure=True), None, "could not be closed"),
        (_Scenario(close_failure=True), None, "could not be closed"),
    ],
)
def test_codex_catalog_failure_is_visible_uncached_and_retires_each_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: _Scenario,
    candidate_model: str | None,
    error_match: str,
) -> None:
    async def exercise() -> None:
        materialized, factory, _catalog = _materialize(
            monkeypatch, tmp_path, scenario
        )
        service = EmployeeConfigurationCatalogService(
            {codex_backend.CODEX_BACKEND_KEY: _adapter(materialized)}
        )

        for _attempt in range(2):
            with pytest.raises(EmployeeConfigurationError, match=error_match):
                await service.catalog(codex_backend.CODEX_BACKEND_KEY, candidate_model)

        assert len(factory.children) == 2
        assert factory.operations.count("close_child") == 2

    asyncio.run(exercise())


def test_codex_initial_configuration_applies_model_then_refreshed_reasoning_and_null_is_native(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def exercise() -> None:
        materialized, factory, _catalog = _materialize(monkeypatch, tmp_path)
        adapter = _adapter(materialized)
        child = await factory.create(
            _employee(), 1, None, None, None
        )
        response = NewSessionResponse(
            session_id="first-ticket-session",
            config_options=_initial_options(),
        )

        await adapter.configure_initial_session(
            child,
            response,
            EmployeeLaunchConfiguration(
                employee_backend="codex",
                employee_launch_model="codex-selected",
                employee_launch_reasoning_effort="max",
            ),
        )
        assert factory.operations == [
            (
                "set_config_option:first-ticket-session:"
                "not-the-codex-model-id:codex-selected"
            ),
            (
                "set_config_option:first-ticket-session:"
                "refreshed-reasoning-option-id:max"
            ),
            "set_session_mode:first-ticket-session:agent-full-access",
        ]

        factory.operations.clear()
        await adapter.configure_initial_session(
            child,
            response,
            EmployeeLaunchConfiguration(
                employee_backend="codex",
                employee_launch_model=None,
                employee_launch_reasoning_effort=None,
            ),
        )
        assert factory.operations == [
            "set_session_mode:first-ticket-session:agent-full-access"
        ]

    asyncio.run(exercise())


def test_codex_disappeared_reasoning_rejects_first_session_before_binding_or_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def exercise() -> None:
        scenario = _Scenario(
            model_response_options=_refreshed_options(
                reasoning_values=(("low", "Low", None),)
            )
        )
        materialized, factory, catalog = _materialize(
            monkeypatch, tmp_path, scenario
        )
        repository = InMemoryAcpBindingRepository()
        registry = _registry(catalog, materialized, repository)
        employee = _employee()

        with pytest.raises(EmployeeConfigurationError, match="does not advertise"):
            await registry.get_or_spawn(employee)

        assert await repository.resolve(employee.employee_id) is None
        assert not any(item.startswith("prompt:") for item in factory.operations)
        assert factory.operations[-1] == "close_child"
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_codex_launch_values_are_not_reapplied_after_first_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def exercise() -> None:
        materialized, factory, catalog = _materialize(monkeypatch, tmp_path)
        repository = InMemoryAcpBindingRepository()
        operations = factory.operations

        async def compare_initial(
            candidate: ConversationSessionBinding,
            configuration: EmployeeLaunchConfiguration,
        ) -> ConversationSessionBinding:
            assert configuration == EmployeeLaunchConfiguration(
                employee_backend="codex",
                employee_launch_model="codex-selected",
                employee_launch_reasoning_effort="max",
            )
            operations.append("publish_first_binding")
            return await repository.compare_and_swap_initial(candidate, configuration)

        registry = _registry(
            catalog,
            materialized,
            repository,
            compare_initial=compare_initial,
        )
        historical_employee = _employee()
        first = await registry.get_or_spawn(historical_employee)
        await first.child.prompt(
            PromptRequest(
                session_id=first.binding.acp_session_id,
                prompt=[],
            )
        )
        assert operations[:7] == [
            "initialize",
            "new_session:codex-session-1-1",
            (
                "set_config_option:codex-session-1-1:"
                "not-the-codex-model-id:codex-selected"
            ),
            (
                "set_config_option:codex-session-1-1:"
                "refreshed-reasoning-option-id:max"
            ),
            "set_session_mode:codex-session-1-1:agent-full-access",
            "publish_first_binding",
            "prompt:codex-session-1-1",
        ]

        operations.append("before_new_conversation")
        replaced = await registry.new_conversation(historical_employee)
        assert replaced.binding.binding_generation == 2
        assert not any(
            item.startswith("set_config_option:")
            for item in operations[operations.index("before_new_conversation") + 1 :]
        )
        assert any(
            item.startswith("set_session_mode:")
            for item in operations[operations.index("before_new_conversation") + 1 :]
        )

        handle = await registry.resolve_runtime_handle(
            replaced.employee.employee_id,
            replaced.binding.binding_generation,
        )
        await registry.fail_runtime_handle(handle)
        await asyncio.sleep(0)
        operations.append("before_replacement_load")
        loaded = await registry.get_or_spawn(historical_employee)
        assert loaded.binding == replaced.binding
        replacement_operations = operations[
            operations.index("before_replacement_load") + 1 :
        ]
        assert any(
            item.startswith("capture_load_session:")
            for item in replacement_operations
        )
        assert not any(
            item.startswith("set_config_option:") for item in replacement_operations
        )
        assert not any(
            item.startswith("set_session_mode:") for item in replacement_operations
        )
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def _employee() -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="employee-codex-provider-test",
        entity_kind="ticket",
        entity_id="ticket-codex-provider-test",
        workspace_roots=(REPOSITORY_ROOT,),
        backend_key="codex",
        employee_launch_model="codex-selected",
        employee_launch_reasoning_effort="max",
    )


def _registry(
    catalog: EmployeeBackendCatalog,
    materialized: Any,
    repository: InMemoryAcpBindingRepository,
    *,
    compare_initial: Any | None = None,
) -> AcpEmployeeRegistry:
    async def discard(_payload: object) -> None:
        return None

    async def deny(_request: object) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    return AcpEmployeeRegistry(
        backend_catalog=catalog,
        materialized_backends=(materialized,),
        resolve_binding=repository.resolve,
        compare_and_swap_binding=repository.compare_and_swap,
        compare_and_swap_initial_binding=(
            compare_initial or repository.compare_and_swap_initial
        ),
        resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
        compare_and_swap_compaction=repository.compare_and_swap_compaction,
        conversation_ingress=discard,
        permission_callback=deny,
    )
