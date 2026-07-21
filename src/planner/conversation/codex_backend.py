"""Locked production definition for the Codex ACP adapter."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    BackendTurnStrategy,
    ReverseServiceCapabilities,
)
from .contracts import ConversationEmployee
from .sdk_child import SdkAcpEmployeeChildFactory

if TYPE_CHECKING:
    from .backend_catalog import EmployeeBackendRegistration

CODEX_BACKEND_KEY: Final = "codex"
CODEX_ACP_AGENT_NAME: Final = "@agentclientprotocol/codex-acp"
CODEX_ACP_AGENT_VERSION: Final = "1.1.4"
CODEX_ACP_VERSION_OUTPUT: Final = "@agentclientprotocol/codex-acp 1.1.4"
CODEX_ACP_ENTRYPOINT_PARTS: Final = (
    "agent_backends",
    "node_modules",
    "@agentclientprotocol",
    "codex-acp",
    "dist",
    "index.js",
)
CODEX_INHERITED_ENVIRONMENT_NAMES: Final = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
    "CODEX_HOME",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
)


def _first_workspace_root(employee: ConversationEmployee) -> Path:
    return employee.workspace_roots[0]


def _require_absolute_path(path: Path, *, field_name: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be absolute")
    return path


def resolve_codex_node_executable(
    configured: Path | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    """Resolve one absolute Node executable without consulting an ambient Codex."""

    candidate = configured
    if candidate is None:
        found = which("node")
        if found is None:
            raise ValueError("Node executable is missing")
        candidate = Path(found)
    _require_absolute_path(candidate, field_name="node_executable")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"Node executable is missing or not executable: {resolved}")
    return resolved


def resolve_codex_acp_entrypoint(repository_root: Path) -> Path:
    """Resolve the only adapter entrypoint admitted by the committed npm lock."""

    _require_absolute_path(repository_root, field_name="repository_root")
    resolved_root = repository_root.resolve(strict=False)
    if not resolved_root.is_dir():
        raise ValueError(f"repository_root is missing: {resolved_root}")
    entrypoint = resolved_root.joinpath(*CODEX_ACP_ENTRYPOINT_PARTS)
    if not entrypoint.is_file():
        raise ValueError(f"Codex ACP adapter entrypoint is missing: {entrypoint}")
    return entrypoint.resolve(strict=True)


def codex_acp_adapter_is_executable(
    *,
    node_executable: Path,
    codex_acp_entrypoint: Path,
) -> bool:
    """Probe only the pinned adapter's version command; never create a session."""

    if (
        not node_executable.is_absolute()
        or not node_executable.is_file()
        or not os.access(node_executable, os.X_OK)
        or not codex_acp_entrypoint.is_absolute()
        or not codex_acp_entrypoint.is_file()
    ):
        return False
    try:
        completed = subprocess.run(
            (str(node_executable), str(codex_acp_entrypoint), "--version"),
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=5,
            env={},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        completed.returncode == 0
        and completed.stdout.strip() == CODEX_ACP_VERSION_OUTPUT
        and completed.stderr == ""
    )


def build_codex_acp_backend_definition(
    *,
    repository_root: Path,
    node_executable: Path,
    app_server_logs: Path,
    turn_strategy: BackendTurnStrategy,
) -> AgentBackendDefinition:
    """Build the exact confined Codex adapter process definition."""

    node = resolve_codex_node_executable(node_executable)
    entrypoint = resolve_codex_acp_entrypoint(repository_root)
    _require_absolute_path(app_server_logs, field_name="app_server_logs")
    logs = app_server_logs.resolve(strict=False)
    return AgentBackendDefinition(
        backend_key=CODEX_BACKEND_KEY,
        argv=(str(node), str(entrypoint)),
        inherited_environment_names=CODEX_INHERITED_ENVIRONMENT_NAMES,
        environment_overrides=(
            ("APP_SERVER_LOGS", str(logs)),
            ("DEFAULT_AUTH_REQUEST", '{"methodId":"api-key"}'),
            ("INITIAL_AGENT_MODE", "agent"),
            ("NO_BROWSER", "1"),
        ),
        expected_agent_name=CODEX_ACP_AGENT_NAME,
        expected_agent_version=CODEX_ACP_AGENT_VERSION,
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=False,
            observes_compaction=True,
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False,
            terminal=False,
            permission=True,
        ),
        working_directory_resolver=_first_workspace_root,
        turn_strategy=turn_strategy,
    )


def build_codex_employee_backend_registration() -> EmployeeBackendRegistration:
    """Return the always-present catalog registration for the Codex runtime."""

    # Local imports keep the provider module below the generic catalog's type
    # boundary while allowing the catalog to import this zero-argument builder.
    from .backend_catalog import (
        EmployeeBackendBuildContext,
        EmployeeBackendRegistration,
        MaterializedEmployeeBackendRegistration,
    )
    from .codex_turn_strategy import CodexAcpTurnStrategy

    def materialize(
        context: EmployeeBackendBuildContext,
    ) -> MaterializedEmployeeBackendRegistration:
        repository_root = context.repository_root
        node = resolve_codex_node_executable()
        strategy = CodexAcpTurnStrategy()
        definition = build_codex_acp_backend_definition(
            repository_root=repository_root,
            node_executable=node,
            app_server_logs=context.data_directory / "codex-acp-logs",
            turn_strategy=strategy,
        )
        entrypoint = Path(definition.argv[1])
        return MaterializedEmployeeBackendRegistration(
            definition=definition,
            child_factory=SdkAcpEmployeeChildFactory(definition),
            is_executable=lambda: codex_acp_adapter_is_executable(
                node_executable=node,
                codex_acp_entrypoint=entrypoint,
            ),
            startup_preflight=None,
        )

    return EmployeeBackendRegistration(CODEX_BACKEND_KEY, materialize)
