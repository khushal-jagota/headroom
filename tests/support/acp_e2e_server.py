"""Test-only uvicorn entry point with the official-SDK ACP subject injected."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from acp.transports import default_environment
from tests.support.probe import build_probe_registry

from planner.conversation.backend_catalog import (
    EmployeeBackendCatalog,
    static_employee_backend_registration,
)
from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.composition import ConversationTestOptions
from planner.conversation.employee_configuration import (
    StableAcpEmployeeSessionConfigurationAdapter,
)
from planner.conversation.sdk_child import SdkAcpEmployeeChildFactory
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.worker_types.configuration import ConfiguredWorkerRuntimeDefinitions

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT = REPOSITORY_ROOT / "tests/support/acp_scripted_agent.py"


class _Strategy:
    def classify_replay(
        self,
        _binding: object,
        replay: tuple[object, ...],
        _boundaries: tuple[object, ...],
    ) -> tuple[object, ...]:
        return replay

    async def steer(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError

    def observe_compaction(self, *args: object, **kwargs: object) -> None:
        return None

    async def capture_compaction(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


def _scripted_definition(backend_key: str) -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key=backend_key,
        argv=(sys.executable, str(SCRIPTED_AGENT)),
        inherited_environment_names=(
            *tuple(default_environment()),
            "ACP_TEST_PROMPT_AUDIT_PATH",
            "ACP_TEST_PROMPT_AUDIT_UDP",
            "ACP_TEST_PROMPT_RELEASE_UDP",
            "ACP_TEST_MALFORMED_DURING_PROMPT",
            "ACP_TEST_CONFIG_AUDIT_PATH",
            "ACP_TEST_CONFIG_FAIL",
            "ACP_TEST_CONFIG_DISAPPEAR_REASONING",
        ),
        environment_overrides=(),
        expected_agent_name="panels-scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(False, False),
        reverse_service_capabilities=ReverseServiceCapabilities(False, False, True),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_Strategy(),
    )


def build_scripted_backends() -> tuple[EmployeeBackendCatalog, ConfiguredWorkerRuntimeDefinitions]:
    definitions = tuple(
        _scripted_definition(key) for key in ("hermes", "codex", "claude")
    )
    catalog = EmployeeBackendCatalog(
        tuple(
            static_employee_backend_registration(
                definition,
                (factory := SdkAcpEmployeeChildFactory(definition)),
                employee_configuration_adapter=(
                    StableAcpEmployeeSessionConfigurationAdapter(
                        definition=definition,
                        child_factory=factory,
                        workspace_root=REPOSITORY_ROOT,
                    )
                ),
            )
            for definition in definitions
        )
    )
    return catalog, ConfiguredWorkerRuntimeDefinitions(build_probe_registry())


def _app() -> object:
    scripted_catalog, scripted_runtime_definitions = build_scripted_backends()
    config = load_config(env=os.environ)
    clock = build_clock(config)
    return create_app(
        config,
        clock,
        lambda: connect(config.db_path),
        conversation_test_options=ConversationTestOptions(
            employee_runtime_definitions=scripted_runtime_definitions,
            employee_backend_catalog=scripted_catalog,
        ),
    )


if __name__ == "__main__":
    uvicorn.run(
        _app(),
        host="127.0.0.1",
        port=int(os.environ["PLAN_PORT"]),
        log_level="warning",
    )
