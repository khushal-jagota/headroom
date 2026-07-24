from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    CancelNotification,
    DeniedOutcome,
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
    SessionNotification,
    SetSessionConfigOptionResponse,
    TextContentBlock,
)
from tests.support.acp_in_memory_binding_repository import (
    InMemoryAcpBindingRepository,
)

from planner.conversation.backend_catalog import (
    EmployeeBackendBuildContext,
    EmployeeBackendCatalog,
    static_employee_backend_registration,
)
from planner.conversation.backend_contracts import AgentBackendDefinition
from planner.conversation.claude_backend import (
    CLAUDE_ACP_AGENT_NAME,
    CLAUDE_ACP_AGENT_VERSION,
    CLAUDE_BACKEND_KEY,
    ClaudeAcpEmployeeChildFactory,
    build_claude_acp_backend_definition,
    build_claude_employee_backend_registration,
)
from planner.conversation.claude_turn_strategy import (
    CLAUDE_COMPACTION_METADATA_KEY,
    CLAUDE_COMPACTION_METADATA_NAMESPACE,
    ClaudeAcpTurnStrategy,
)
from planner.conversation.contracts import ConversationEmployee
from planner.conversation.employee_configuration import (
    EmployeeConfigurationCatalogOption,
    EmployeeConfigurationCatalogService,
    EmployeeConfigurationError,
    StableAcpEmployeeSessionConfigurationAdapter,
)
from planner.conversation.employee_registry import AcpEmployeeRegistry
from planner.conversation.wire_contracts import ProtocolUpdateRejectedPayload
from planner.tickets.contracts import EmployeeLaunchConfiguration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NODE_EXECUTABLE = Path(shutil.which("node") or "").resolve()


def _definition() -> AgentBackendDefinition:
    return build_claude_acp_backend_definition(
        repository_root=REPOSITORY_ROOT,
        node_executable=NODE_EXECUTABLE,
        turn_strategy=ClaudeAcpTurnStrategy(),
    )


def _select(
    option_id: str,
    category: str,
    current: str,
    values: tuple[tuple[str, str, str | None], ...],
) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        type="select",
        id=option_id,
        name=category,
        category=category,
        current_value=current,
        options=[
            SessionConfigSelectOption(value=value, name=name, description=description)
            for value, name, description in values
        ],
    )


def _initial_options() -> list[SessionConfigOptionSelect]:
    return [
        _select(
            "provider-choice-17",
            "model",
            "claude-opus",
            (
                ("claude-opus", "Opus", "Most capable"),
                ("claude-sonnet", "Sonnet", "Balanced"),
                ("claude-haiku", "Haiku", ""),
            ),
        ),
        _select("behavior-mode", "mode", "code", (("code", "Code", None),)),
        _select("speed-switch", "fast", "off", (("off", "Off", None),)),
        _select("agent-choice", "agent", "default", (("default", "Default", None),)),
        _select(
            "thinking-choice-initial",
            "thought_level",
            "default",
            (("default", "Default", "Claude default"),),
        ),
    ]


def _refreshed_options(model: str) -> list[SessionConfigOptionSelect]:
    model_option = _select(
        "provider-choice-refreshed",
        "model",
        model,
        (
            ("claude-opus", "Opus", "Most capable"),
            ("claude-sonnet", "Sonnet", "Balanced"),
            ("claude-haiku", "Haiku", ""),
        ),
    )
    if model == "claude-haiku":
        return [
            model_option,
            _select("behavior-mode", "mode", "code", (("code", "Code", None),)),
        ]
    return [
        model_option,
        _select(
            "thinking-choice-refreshed",
            "thought_level",
            "default",
            (
                ("default", "Default", "Claude default"),
                ("high", "High", "More thinking"),
            ),
        ),
        _select("behavior-mode", "mode", "code", (("code", "Code", None),)),
    ]


@dataclass
class _Script:
    initial_options: list[SessionConfigOptionSelect] = field(
        default_factory=_initial_options
    )
    set_failure: bool = False
    close_session_failure: bool = False
    child_close_failure: bool = False


class _ScriptedClaudeChild:
    def __init__(
        self,
        *,
        generation: int,
        operations: list[tuple[str, str | None, str | None]],
        script: _Script,
    ) -> None:
        self.generation = generation
        self.alive = True
        self.supports_session_fork = False
        self._operations = operations
        self._script = script
        self._session_number = 0

    async def initialize(self, request: object) -> InitializeResponse:
        del request
        self._operations.append(("initialize", None, None))
        return InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name=CLAUDE_ACP_AGENT_NAME,
                version=CLAUDE_ACP_AGENT_VERSION,
            ),
        )

    async def new_session(self, request: object) -> NewSessionResponse:
        del request
        self._operations.append(("new_session", None, None))
        self._session_number += 1
        return NewSessionResponse(
            session_id=f"claude-session-{self.generation}-{self._session_number}",
            config_options=self._script.initial_options,
        )

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        del session_id
        self._operations.append(("set_config_option", config_id, value))
        if self._script.set_failure:
            raise RuntimeError("scripted set failure")
        if config_id in {"provider-choice-17", "provider-choice-refreshed"}:
            return SetSessionConfigOptionResponse(
                config_options=_refreshed_options(value)
            )
        if config_id == "thinking-choice-refreshed":
            options = _refreshed_options("claude-sonnet")
            reasoning = next(
                option for option in options if option.category == "thought_level"
            )
            options[options.index(reasoning)] = reasoning.model_copy(
                update={"current_value": value}
            )
            return SetSessionConfigOptionResponse(config_options=options)
        raise RuntimeError(f"unexpected configuration option {config_id!r}")

    async def set_session_mode(self, session_id: str, mode_id: str) -> None:
        self._operations.append(("set_session_mode", session_id, mode_id))

    async def close_session(self, session_id: str) -> None:
        self._operations.append(("close_session", session_id, None))
        if self._script.close_session_failure:
            raise RuntimeError("scripted session close failure")

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        self._operations.append(("load_session", request.session_id, None))
        return LoadSessionResponse()

    async def capture_load_session(
        self, request: LoadSessionRequest, private_ingress: Any
    ) -> LoadSessionResponse:
        await private_ingress(
            SessionNotification(
                session_id=request.session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text="Compacting..."),
                ),
            )
        )
        return await self.load_session(request)

    async def fork_session(self, request: object) -> object:
        raise AssertionError(f"Claude must not fork sessions: {request!r}")

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self._operations.append(("prompt", request.session_id, None))
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        self._operations.append(("close", None, None))
        self.alive = False
        if self._script.child_close_failure:
            raise RuntimeError("scripted child close failure")


class _ScriptedClaudeFactory:
    def __init__(self, script: _Script | None = None) -> None:
        self.script = script or _Script()
        self.operations: list[tuple[str, str | None, str | None]] = []
        self.children: list[_ScriptedClaudeChild] = []

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: object,
        permission_callback: object,
        death_callback: object,
    ) -> _ScriptedClaudeChild:
        del employee, update_ingress, permission_callback, death_callback
        child = _ScriptedClaudeChild(
            generation=generation,
            operations=self.operations,
            script=self.script,
        )
        self.children.append(child)
        return child


def _provider(
    *, script: _Script | None = None
) -> tuple[
    AgentBackendDefinition,
    ClaudeAcpEmployeeChildFactory,
    StableAcpEmployeeSessionConfigurationAdapter,
    _ScriptedClaudeFactory,
]:
    definition = _definition()
    delegate_factory = _ScriptedClaudeFactory(script)
    child_factory = ClaudeAcpEmployeeChildFactory(
        definition, delegate_factory=delegate_factory
    )
    adapter = StableAcpEmployeeSessionConfigurationAdapter(
        definition=definition,
        child_factory=child_factory,
        workspace_root=REPOSITORY_ROOT,
        full_access_mode="bypassPermissions",
    )
    return definition, child_factory, adapter, delegate_factory


async def _discard(_payload: object) -> None:
    return None


async def _deny(_request: object) -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


async def _ignore_death(_error: BaseException | None) -> None:
    return None


def _employee(
    *, model: str | None = "claude-sonnet", reasoning: str | None = "default"
) -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="ticket-claude",
        entity_kind="ticket",
        entity_id="ticket-claude",
        workspace_roots=(REPOSITORY_ROOT,),
        backend_key=CLAUDE_BACKEND_KEY,
        employee_launch_model=model,
        employee_launch_reasoning_effort=reasoning,
    )


def test_materialized_claude_registration_uses_its_decorated_durable_factory_for_configuration(
    tmp_path: Path,
) -> None:
    employee_workspace_root = tmp_path / "employee-workspace"
    materialized = build_claude_employee_backend_registration().runtime_builder(
        EmployeeBackendBuildContext(
            data_directory=tmp_path,
            repository_root=REPOSITORY_ROOT,
            employee_workspace_root=employee_workspace_root,
        )
    )

    adapter = materialized.employee_configuration_adapter
    assert isinstance(materialized.child_factory, ClaudeAcpEmployeeChildFactory)
    assert isinstance(adapter, StableAcpEmployeeSessionConfigurationAdapter)
    assert adapter is materialized.resolved_employee_configuration_adapter()
    assert adapter._child_factory is materialized.child_factory
    assert adapter._definition is materialized.definition
    assert Path(materialized.definition.argv[1]).is_relative_to(REPOSITORY_ROOT)
    assert adapter._workspace_root == employee_workspace_root
    assert materialized.startup_preflight is not None
    assert materialized.is_executable() is False


def test_claude_catalog_uses_semantic_categories_exact_values_and_model_scoped_cache() -> None:
    async def exercise() -> None:
        _definition_value, _factory, adapter, delegate = _provider()
        service = EmployeeConfigurationCatalogService({CLAUDE_BACKEND_KEY: adapter})

        sonnet = await service.catalog(CLAUDE_BACKEND_KEY, "claude-sonnet")
        cached = await service.catalog(CLAUDE_BACKEND_KEY, "claude-sonnet")
        haiku = await service.catalog(CLAUDE_BACKEND_KEY, "claude-haiku")

        assert cached is sonnet
        assert sonnet.native_model == "claude-opus"
        assert sonnet.models == (
            EmployeeConfigurationCatalogOption(
                value="claude-opus", label="Opus", description="Most capable"
            ),
            EmployeeConfigurationCatalogOption(
                value="claude-sonnet", label="Sonnet", description="Balanced"
            ),
            EmployeeConfigurationCatalogOption(
                value="claude-haiku", label="Haiku", description=None
            ),
        )
        assert sonnet.reasoning_supported is True
        assert sonnet.native_reasoning_effort == "default"
        assert sonnet.reasoning_efforts == (
            EmployeeConfigurationCatalogOption(
                value="default", label="Default", description="Claude default"
            ),
            EmployeeConfigurationCatalogOption(
                value="high", label="High", description="More thinking"
            ),
        )
        assert haiku.reasoning_supported is False
        assert haiku.native_reasoning_effort is None
        assert haiku.reasoning_efforts == ()
        assert len(delegate.children) == 2
        assert delegate.operations == [
            ("initialize", None, None),
            ("new_session", None, None),
            ("set_config_option", "provider-choice-17", "claude-sonnet"),
            ("close_session", "claude-session-1-1", None),
            ("close", None, None),
            ("initialize", None, None),
            ("new_session", None, None),
            ("set_config_option", "provider-choice-17", "claude-haiku"),
            ("close_session", "claude-session-1-1", None),
            ("close", None, None),
        ]

    asyncio.run(exercise())


def test_claude_configuration_preserves_explicit_default_and_decorator_normalization() -> None:
    async def exercise() -> None:
        _definition_value, factory, adapter, delegate = _provider()
        child = await factory.create(
            _employee(), 1, _discard, _deny, _ignore_death
        )
        response = NewSessionResponse(
            session_id="claude-session-1-1", config_options=_initial_options()
        )

        await adapter.configure_initial_session(
            child,
            response,
            EmployeeLaunchConfiguration(
                employee_backend=CLAUDE_BACKEND_KEY,
                employee_launch_model="claude-sonnet",
                employee_launch_reasoning_effort="default",
            ),
        )
        assert delegate.operations == [
            ("set_config_option", "provider-choice-17", "claude-sonnet"),
            ("set_config_option", "thinking-choice-refreshed", "default"),
            ("set_session_mode", "claude-session-1-1", "bypassPermissions"),
        ]

        delegate.operations.clear()
        await adapter.configure_initial_session(
            child,
            response,
            EmployeeLaunchConfiguration(
                employee_backend=CLAUDE_BACKEND_KEY,
                employee_launch_model=None,
                employee_launch_reasoning_effort=None,
            ),
        )
        assert delegate.operations == [
            ("set_session_mode", "claude-session-1-1", "bypassPermissions")
        ]

        captured: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

        async def capture(
            payload: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            captured.append(payload)

        load_request = LoadSessionRequest(
            cwd=str(REPOSITORY_ROOT),
            additional_directories=[],
            mcp_servers=[],
            session_id="claude-session-1-1",
        )
        await child.capture_load_session(load_request, capture)
        assert isinstance(captured[0], SessionNotification)
        assert captured[0].update.session_update == "session_info_update"
        assert captured[0].update.field_meta == {
            CLAUDE_COMPACTION_METADATA_NAMESPACE: {
                CLAUDE_COMPACTION_METADATA_KEY: "Compacting..."
            }
        }

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("candidate_model", "script"),
    [
        ("unknown-alias", _Script()),
        (
            None,
            _Script(
                initial_options=[
                    _select("mode", "mode", "code", (("code", "Code", None),))
                ]
            ),
        ),
        (
            None,
            _Script(
                initial_options=[
                    _initial_options()[0],
                    _select(
                        "duplicate-model",
                        "model",
                        "claude-opus",
                        (("claude-opus", "Opus", None),),
                    ),
                ]
            ),
        ),
        (
            None,
            _Script(
                initial_options=[
                    _select(
                        "provider-choice-17",
                        "model",
                        "not-advertised",
                        (("claude-opus", "Opus", None),),
                    )
                ]
            ),
        ),
        ("claude-sonnet", _Script(set_failure=True)),
        (None, _Script(close_session_failure=True)),
        (None, _Script(child_close_failure=True)),
    ],
)
def test_claude_catalog_failures_are_visible_uncached_and_retire_children(
    candidate_model: str | None, script: _Script
) -> None:
    async def exercise() -> None:
        _definition_value, _factory, adapter, delegate = _provider(script=script)
        service = EmployeeConfigurationCatalogService({CLAUDE_BACKEND_KEY: adapter})

        for _attempt in range(2):
            with pytest.raises(EmployeeConfigurationError):
                await service.catalog(CLAUDE_BACKEND_KEY, candidate_model)

        assert len(delegate.children) == 2
        assert all(not child.alive for child in delegate.children)

    asyncio.run(exercise())


def test_first_claude_session_configures_before_binding_and_never_reapplies_afterward() -> None:
    async def exercise() -> None:
        definition, factory, adapter, delegate = _provider()
        repository = InMemoryAcpBindingRepository()
        backend_catalog = EmployeeBackendCatalog(
            (
                static_employee_backend_registration(
                    definition,
                    factory,
                    employee_configuration_adapter=adapter,
                ),
            )
        )
        employee = _employee()

        async def compare_initial(candidate: Any, configuration: Any) -> Any:
            delegate.operations.append(("binding", None, None))
            return await repository.compare_and_swap_initial(candidate, configuration)

        async def resolve_employee(_employee_id: str) -> ConversationEmployee:
            return employee.model_copy(
                update={
                    "employee_launch_model": None,
                    "employee_launch_reasoning_effort": None,
                }
            )

        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=backend_catalog.materialize(
                EmployeeBackendBuildContext(
                    data_directory=REPOSITORY_ROOT / "data",
                    employee_workspace_root=REPOSITORY_ROOT,
                )
            ),
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            compare_and_swap_initial_binding=compare_initial,
            resolve_employee=resolve_employee,
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=repository.compare_and_swap_compaction,
            conversation_ingress=_discard,
            permission_callback=_deny,
        )

        record = await registry.get_or_spawn(employee)
        await record.child.prompt(
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[
                    TextContentBlock(type="text", text="Start work")
                ],
            )
        )
        assert delegate.operations[:7] == [
            ("initialize", None, None),
            ("new_session", None, None),
            ("set_config_option", "provider-choice-17", "claude-sonnet"),
            ("set_config_option", "thinking-choice-refreshed", "default"),
            ("set_session_mode", "claude-session-1-1", "bypassPermissions"),
            ("binding", None, None),
            ("prompt", "claude-session-1-1", None),
        ]

        delegate.operations.clear()
        await record.child.close()
        replacement = await registry.get_or_spawn(employee)
        assert not any(operation[0] == "set_config_option" for operation in delegate.operations)
        assert ("load_session", "claude-session-1-1", None) in delegate.operations
        assert (
            "set_session_mode",
            "claude-session-1-1",
            "bypassPermissions",
        ) in delegate.operations
        assert replacement.employee.employee_launch_model is None
        assert replacement.employee.employee_launch_reasoning_effort is None

        delegate.operations.clear()
        await registry.new_conversation(employee)
        assert not any(operation[0] == "set_config_option" for operation in delegate.operations)
        assert ("new_session", None, None) in delegate.operations
        assert any(
            operation[0] == "set_session_mode"
            and operation[2] == "bypassPermissions"
            for operation in delegate.operations
        )
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [
        ("claude-sonnet", "missing-effort"),
        ("claude-haiku", "default"),
    ],
)
def test_invalid_claude_launch_configuration_publishes_no_binding_or_prompt(
    model: str, reasoning: str
) -> None:
    async def exercise() -> None:
        definition, factory, adapter, delegate = _provider()
        repository = InMemoryAcpBindingRepository()
        backend_catalog = EmployeeBackendCatalog(
            (
                static_employee_backend_registration(
                    definition,
                    factory,
                    employee_configuration_adapter=adapter,
                ),
            )
        )
        employee = _employee(model=model, reasoning=reasoning)
        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=backend_catalog.materialize(
                EmployeeBackendBuildContext(
                    data_directory=REPOSITORY_ROOT / "data",
                    employee_workspace_root=REPOSITORY_ROOT,
                )
            ),
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            compare_and_swap_initial_binding=repository.compare_and_swap_initial,
            resolve_employee=lambda _employee_id: asyncio.sleep(0, result=employee),
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=repository.compare_and_swap_compaction,
            conversation_ingress=_discard,
            permission_callback=_deny,
        )

        with pytest.raises(EmployeeConfigurationError):
            await registry.get_or_spawn(employee)
        assert await repository.resolve(employee.employee_id) is None
        assert not any(operation[0] == "binding" for operation in delegate.operations)
        assert not any(operation[0] == "prompt" for operation in delegate.operations)
        assert delegate.children and delegate.children[-1].alive is False
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())
