from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from acp.transports import default_environment

from planner.conversation.backend_catalog import EmployeeBackendBuildContext
from planner.conversation.codex_backend import (
    CODEX_ACP_AGENT_VERSION,
    CODEX_BACKEND_KEY,
    CODEX_INHERITED_ENVIRONMENT_NAMES,
    build_codex_acp_backend_definition,
    build_codex_employee_backend_registration,
    codex_acp_adapter_is_executable,
)
from planner.conversation.codex_turn_strategy import CodexAcpTurnStrategy
from planner.conversation.contracts import ConversationEmployee
from planner.conversation.sdk_child import (
    SdkAcpEmployeeChildFactory,
    build_confined_child_environment,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NODE_EXECUTABLE = Path(shutil.which("node") or "")


def _employee(entity_kind: str = "ticket") -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="employee-codex",
        entity_kind=entity_kind,  # type: ignore[arg-type]
        entity_id="ticket-codex" if entity_kind == "ticket" else "chief",
        workspace_roots=(Path("/workspace/first"), Path("/workspace/second")),
        backend_key=CODEX_BACKEND_KEY,
    )


def _definition(
    *,
    node_executable: Path = NODE_EXECUTABLE,
    repository_root: Path = REPOSITORY_ROOT,
    app_server_logs: Path = Path("/srv/panels/codex-acp-logs"),
):
    return build_codex_acp_backend_definition(
        repository_root=repository_root,
        node_executable=node_executable,
        app_server_logs=app_server_logs,
        turn_strategy=CodexAcpTurnStrategy(),
    )


def test_codex_definition_is_locked_confined_and_permission_only() -> None:
    definition = _definition()

    assert definition.backend_key == CODEX_BACKEND_KEY == "codex"
    assert definition.argv == (
        str(NODE_EXECUTABLE.resolve()),
        str(
            REPOSITORY_ROOT
            / "agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ),
    )
    assert definition.expected_agent_name == "@agentclientprotocol/codex-acp"
    assert definition.expected_agent_version == CODEX_ACP_AGENT_VERSION == "1.1.4"
    assert definition.inherited_environment_names == CODEX_INHERITED_ENVIRONMENT_NAMES
    assert definition.environment_overrides == (
        ("APP_SERVER_LOGS", "/srv/panels/codex-acp-logs"),
        ("DEFAULT_AUTH_REQUEST", '{"methodId":"api-key"}'),
        ("INITIAL_AGENT_MODE", "agent-full-access"),
        ("NO_BROWSER", "1"),
    )
    assert definition.turn_capabilities.supports_steer is False
    assert definition.turn_capabilities.observes_compaction is True
    assert (
        definition.turn_capabilities.requires_fresh_child_after_requested_cancel
        is False
    )
    assert definition.reverse_service_capabilities.filesystem is False
    assert definition.reverse_service_capabilities.terminal is False
    assert definition.reverse_service_capabilities.permission is True
    assert definition.working_directory_for(_employee()) == Path("/workspace/first")


def test_codex_environment_keeps_only_declared_auth_and_panels_identity() -> None:
    ambient = {
        **default_environment(),
        "CODEX_HOME": "/srv/codex-home",
        "CODEX_API_KEY": "codex-key",
        "OPENAI_API_KEY": "openai-key",
        "CODEX_CONFIG": "/ambient/config",
        "CODEX_PATH": "/ambient/codex",
        "MODEL_PROVIDER": "ambient-provider",
        "APP_SERVER_LOGS": "/ambient/logs",
        "DEFAULT_AUTH_REQUEST": "ambient",
        "INITIAL_AGENT_MODE": "ambient",
        "NO_BROWSER": "0",
        "PLAN_ACTOR": "stale",
        "PLAN_TICKET_ID": "stale",
    }

    environment = build_confined_child_environment(
        _definition(), _employee(), ambient_environment=ambient
    )

    assert environment == {
        **default_environment(),
        "CODEX_HOME": "/srv/codex-home",
        "CODEX_API_KEY": "codex-key",
        "OPENAI_API_KEY": "openai-key",
        "APP_SERVER_LOGS": "/srv/panels/codex-acp-logs",
        "DEFAULT_AUTH_REQUEST": '{"methodId":"api-key"}',
        "INITIAL_AGENT_MODE": "agent-full-access",
        "NO_BROWSER": "1",
        "PLAN_ACTOR": "worker",
        "PLAN_TICKET_ID": "ticket-codex",
    }
    assert "CODEX_PATH" not in environment
    assert "CODEX_CONFIG" not in environment
    assert "MODEL_PROVIDER" not in environment


@pytest.mark.parametrize(
    ("repository_root", "node_executable", "app_server_logs", "message"),
    [
        (
            Path("relative/repository"),
            NODE_EXECUTABLE,
            Path("/logs"),
            "repository_root must be absolute",
        ),
        (
            REPOSITORY_ROOT,
            Path("relative/node"),
            Path("/logs"),
            "node_executable must be absolute",
        ),
        (
            REPOSITORY_ROOT,
            Path("/missing/panels-node"),
            Path("/logs"),
            "Node executable is missing",
        ),
        (
            REPOSITORY_ROOT,
            NODE_EXECUTABLE,
            Path("relative/logs"),
            "app_server_logs must be absolute",
        ),
    ],
)
def test_codex_definition_rejects_unresolved_runtime_paths(
    repository_root: Path,
    node_executable: Path,
    app_server_logs: Path,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_codex_acp_backend_definition(
            repository_root=repository_root,
            node_executable=node_executable,
            app_server_logs=app_server_logs,
            turn_strategy=CodexAcpTurnStrategy(),
        )


def test_codex_definition_rejects_missing_or_wrong_adapter_version(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing-root"
    missing_root.mkdir()
    with pytest.raises(ValueError, match="Codex ACP adapter entrypoint is missing"):
        _definition(repository_root=missing_root)

    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\nprintf 'wrong 9.9.9\\n'\n")
    fake_node.chmod(0o755)
    entrypoint = (
        REPOSITORY_ROOT
        / "agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
    )
    assert not codex_acp_adapter_is_executable(
        node_executable=fake_node,
        codex_acp_entrypoint=entrypoint,
    )

    non_executable_node = tmp_path / "not-executable-node"
    non_executable_node.write_text("not executable")
    assert not codex_acp_adapter_is_executable(
        node_executable=non_executable_node,
        codex_acp_entrypoint=entrypoint,
    )


def test_zero_arg_codex_registration_materializes_sdk_factory_and_exact_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_home = tmp_path / "user-home" / ".codex"
    (native_home / "sessions").mkdir(parents=True)
    (native_home / "auth.json").write_text("auth", encoding="utf-8")
    (native_home / "sessions" / "current.json").write_text("session", encoding="utf-8")
    monkeypatch.setenv("HOME", str(native_home.parent))
    registration = build_codex_employee_backend_registration()
    assert registration.backend_key == CODEX_BACKEND_KEY

    materialized = registration.runtime_builder(
        EmployeeBackendBuildContext(
            repository_root=REPOSITORY_ROOT,
            data_directory=tmp_path,
        )
    )
    assert materialized.definition.backend_key == CODEX_BACKEND_KEY
    assert isinstance(materialized.child_factory, SdkAcpEmployeeChildFactory)
    assert materialized.child_factory.definition is materialized.definition
    assert materialized.definition.environment_overrides[0] == (
        "APP_SERVER_LOGS",
        str(tmp_path / "codex-acp-logs"),
    )
    assert (native_home / "skills").resolve() == (tmp_path / "skills").resolve()
    assert (native_home / "auth.json").read_text() == "auth"
    assert (native_home / "sessions" / "current.json").read_text() == "session"
    assert materialized.is_executable() is True
    assert materialized.startup_preflight is None


def test_codex_version_probe_uses_the_locked_adapter_without_auth_or_model() -> None:
    definition = _definition()
    assert codex_acp_adapter_is_executable(
        node_executable=Path(definition.argv[0]),
        codex_acp_entrypoint=Path(definition.argv[1]),
    )
