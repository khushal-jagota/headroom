from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentCapabilities,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.employee_configuration import (
    EmployeeConfigurationCatalog,
    EmployeeConfigurationCatalogOption,
    EmployeeConfigurationCatalogService,
    EmployeeConfigurationError,
    StableAcpEmployeeSessionConfigurationAdapter,
)
from planner.tickets.contracts import EmployeeLaunchConfiguration


class _Strategy:
    def classify_replay(self, *args: Any, **kwargs: Any) -> tuple[object, ...]:
        return ()

    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _definition() -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key="scripted",
        argv=("/scripted",),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name="scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(False, False),
        reverse_service_capabilities=ReverseServiceCapabilities(False, False, True),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_Strategy(),
    )


def _select(
    config_id: str,
    category: str,
    current: str,
    values: tuple[tuple[str, str, str | None], ...],
) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        type="select",
        id=config_id,
        name=category,
        category=category,
        current_value=current,
        options=[
            SessionConfigSelectOption(value=value, name=label, description=description)
            for value, label, description in values
        ],
    )


def _initial_options() -> list[SessionConfigOptionSelect]:
    return [
        _select(
            "provider-model-id",
            "model",
            "model-a",
            (("model-a", "Model A", "Default model"), ("model-b", "Model B", "")),
        ),
        _select(
            "provider-effort-initial",
            "thought_level",
            "low",
            (("low", "Low", None),),
        ),
    ]


@dataclass
class _Child:
    operations: list[tuple[str, str | None, str | None]]
    generation: int = 1
    alive: bool = True
    supports_session_fork: bool = False

    async def initialize(self, request: object) -> InitializeResponse:
        del request
        self.operations.append(("initialize", None, None))
        return InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(name="scripted-agent", version="1.0.0"),
        )

    async def new_session(self, request: object) -> NewSessionResponse:
        del request
        self.operations.append(("new_session", None, None))
        return NewSessionResponse(
            session_id="temporary-session", config_options=_initial_options()
        )

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        self.operations.append(("set_config_option", config_id, value))
        assert session_id == "temporary-session"
        if config_id == "provider-model-id":
            return SetSessionConfigOptionResponse(
                config_options=[
                    _select(
                        "provider-model-refreshed",
                        "model",
                        value,
                        (
                            ("model-a", "Model A", "Default model"),
                            ("model-b", "Model B", ""),
                        ),
                    ),
                    _select(
                        "provider-effort-refreshed",
                        "thought_level",
                        "high",
                        (("high", "High", "Deep reasoning"),),
                    ),
                ]
            )
        return SetSessionConfigOptionResponse(
            config_options=[
                _select(
                    "provider-effort-refreshed",
                    "thought_level",
                    value,
                    (("high", "High", "Deep reasoning"),),
                )
            ]
        )

    async def close_session(self, session_id: str) -> None:
        self.operations.append(("close_session", session_id, None))

    async def close(self) -> None:
        self.operations.append(("close", None, None))
        self.alive = False


class _Factory:
    def __init__(self) -> None:
        self.children: list[_Child] = []

    async def create(self, *args: Any, **kwargs: Any) -> _Child:
        del args, kwargs
        child = _Child([])
        self.children.append(child)
        return child


def test_catalog_discovery_is_cached_and_closes_temporary_session(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        factory = _Factory()
        adapter = StableAcpEmployeeSessionConfigurationAdapter(
            definition=_definition(), child_factory=factory, workspace_root=tmp_path
        )
        service = EmployeeConfigurationCatalogService({"scripted": adapter})

        first = await service.catalog("scripted", "model-b")
        second = await service.catalog("scripted", "model-b")

        assert first is second
        assert first == EmployeeConfigurationCatalog(
            employee_backend="scripted",
            candidate_model="model-b",
            native_model="model-a",
            models=(
                EmployeeConfigurationCatalogOption(
                    value="model-a", label="Model A", description="Default model"
                ),
                EmployeeConfigurationCatalogOption(
                    value="model-b", label="Model B", description=None
                ),
            ),
            reasoning_supported=True,
            native_reasoning_effort="high",
            reasoning_efforts=(
                EmployeeConfigurationCatalogOption(
                    value="high", label="High", description="Deep reasoning"
                ),
            ),
        )
        assert len(factory.children) == 1
        assert factory.children[0].operations == [
            ("initialize", None, None),
            ("new_session", None, None),
            ("set_config_option", "provider-model-id", "model-b"),
            ("close_session", "temporary-session", None),
            ("close", None, None),
        ]

    asyncio.run(exercise())


def test_semantic_adapter_applies_model_before_reasoning_from_refreshed_options(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        factory = _Factory()
        adapter = StableAcpEmployeeSessionConfigurationAdapter(
            definition=_definition(), child_factory=factory, workspace_root=tmp_path
        )
        child = _Child([])
        await adapter.configure_initial_session(
            child,
            NewSessionResponse(
                session_id="temporary-session", config_options=_initial_options()
            ),
            EmployeeLaunchConfiguration(
                employee_backend="scripted",
                employee_launch_model="model-b",
                employee_launch_reasoning_effort="high",
            ),
        )
        assert child.operations == [
            ("set_config_option", "provider-model-id", "model-b"),
            ("set_config_option", "provider-effort-refreshed", "high"),
        ]

    asyncio.run(exercise())


def test_catalog_failure_is_not_cached_and_retry_can_succeed() -> None:
    class _FlakyAdapter:
        backend_key = "scripted"

        def __init__(self) -> None:
            self.calls = 0

        async def discover_catalog(
            self, candidate_model: str | None
        ) -> EmployeeConfigurationCatalog:
            self.calls += 1
            if self.calls == 1:
                raise EmployeeConfigurationError("try again")
            return EmployeeConfigurationCatalog(
                employee_backend="scripted",
                candidate_model=candidate_model,
                native_model=None,
                models=(),
                reasoning_supported=False,
                native_reasoning_effort=None,
                reasoning_efforts=(),
            )

        async def configure_initial_session(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

    async def exercise() -> None:
        adapter = _FlakyAdapter()
        service = EmployeeConfigurationCatalogService({"scripted": adapter})
        with pytest.raises(EmployeeConfigurationError, match="try again"):
            await service.catalog("scripted", None)
        result = await service.catalog("scripted", None)
        assert result.employee_backend == "scripted"
        assert adapter.calls == 2

    asyncio.run(exercise())
