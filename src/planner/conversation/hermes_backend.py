"""Pinned Hermes ACP backend definition."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from .backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    BackendTurnStrategy,
    ReverseServiceCapabilities,
)
from .contracts import ConversationEmployee

HERMES_BACKEND_KEY: Final = "hermes"
HERMES_ACP_AGENT_VERSION = "0.18.2"
HERMES_INHERITED_ENVIRONMENT_NAMES = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
)


def _first_workspace_root(employee: ConversationEmployee) -> Path:
    return employee.workspace_roots[0]


def build_hermes_acp_backend_definition(
    *,
    hermes_executable: Path,
    hermes_home: Path,
    hermes_source_root: Path,
    turn_strategy: BackendTurnStrategy,
) -> AgentBackendDefinition:
    for field_name, path in (
        ("hermes_executable", hermes_executable),
        ("hermes_home", hermes_home),
        ("hermes_source_root", hermes_source_root),
    ):
        if not path.is_absolute():
            raise ValueError(f"{field_name} must be absolute")
    if hermes_executable.name != "hermes":
        raise ValueError("hermes_executable basename must be 'hermes'")
    return AgentBackendDefinition(
        backend_key=HERMES_BACKEND_KEY,
        argv=(str(hermes_executable), "acp"),
        inherited_environment_names=HERMES_INHERITED_ENVIRONMENT_NAMES,
        environment_overrides=(
            ("HERMES_HOME", str(hermes_home)),
            ("HERMES_PYTHON_SRC_ROOT", str(hermes_source_root)),
        ),
        expected_agent_name="hermes-agent",
        expected_agent_version=HERMES_ACP_AGENT_VERSION,
        turn_capabilities=BackendTurnCapabilities(supports_steer=True, observes_compaction=True),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False, terminal=False, permission=True
        ),
        working_directory_resolver=_first_workspace_root,
        turn_strategy=turn_strategy,
    )
