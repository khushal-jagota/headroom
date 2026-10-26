"""Test-process-only native mappings; never imported by Panels runtime code.

Observation hooks sit downstream of the mapping functions, at the real SDK/RPC
boundary. A missing override therefore fails configuration checks before allow can
silently pass under production's full access.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from acp.client.connection import ClientSideConnection
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from planner.conversation.backends import claude_agent_sdk as claude
from planner.conversation.backends import hermes_acp as hermes
from planner.conversation.backends.codex_app_server import adapter as codex
from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.client import CodexAppServerClient
from planner.conversation.contracts import ConversationBackendKey


def install_permission_mapping(
    monkeypatch: pytest.MonkeyPatch,
    backend: ConversationBackendKey,
    observations: list[dict[str, Any]],
) -> None:
    if backend is ConversationBackendKey.codex:
        monkeypatch.setattr(codex, "_approval_policy", lambda access: "untrusted")
        monkeypatch.setattr(codex, "_approvals_reviewer", lambda access: "user")
        monkeypatch.setattr(codex, "_thread_sandbox", lambda access: "workspace-write")
        monkeypatch.setattr(
            codex,
            "_turn_sandbox_policy",
            lambda access: bindings.WorkspaceWriteSandboxPolicy(type="workspaceWrite"),
        )
        original = CodexAppServerClient.begin_request

        async def observe_codex(
            self: CodexAppServerClient, method: str, params: Mapping[str, Any] | None = None
        ) -> Any:
            if method in {"thread/start", "thread/resume", "turn/start"}:
                values = dict(params or {})
                observed = {
                    "method": method,
                    "approvalPolicy": values.get("approvalPolicy"),
                    "approvalsReviewer": values.get("approvalsReviewer"),
                    "sandbox": values.get("sandbox"),
                    "sandboxPolicy": values.get("sandboxPolicy"),
                }
                observations.append(observed)
                assert observed["approvalPolicy"] == "untrusted"
                assert observed["approvalsReviewer"] == "user"
                if method == "turn/start":
                    assert isinstance(observed["sandboxPolicy"], dict)
                    assert observed["sandboxPolicy"]["type"] == "workspaceWrite"
                else:
                    assert observed["sandbox"] == "workspace-write"
            return await original(self, method, params)

        monkeypatch.setattr(CodexAppServerClient, "begin_request", observe_codex)
    elif backend is ConversationBackendKey.claude:
        monkeypatch.setattr(claude, "_permission_mode", lambda access: "default")
        original_command = SubprocessCLITransport._build_command

        def observe_claude(self: Any) -> list[str]:
            command: list[str] = original_command(self)
            options = self._options
            # The production catalogue uses an SDK startup handshake, not a mapped
            # conversation. Preserve its command and native defaults unchanged.
            is_catalogue = (
                options.max_turns == 1
                and options.permission_mode is None
                and options.can_use_tool is None
                and options.model is None
                and options.cwd is None
                and options.system_prompt is None
            )
            if is_catalogue:
                return command
            index = command.index("--permission-mode")
            mode = command[index + 1]
            observations.append({"cli_permission_mode": mode})
            assert mode == "default"
            assert "--dangerously-skip-permissions" not in command
            return command

        monkeypatch.setattr(SubprocessCLITransport, "_build_command", observe_claude)
    else:
        monkeypatch.setattr(
            hermes, "_access_environment", lambda access: (("HERMES_YOLO_MODE", "0"),)
        )
        original_environment = hermes._child_environment

        def observe_environment(launch: Any, start: Any) -> dict[str, str]:
            environment = original_environment(launch, start)
            value = environment.get("HERMES_YOLO_MODE")
            observations.append({"child_HERMES_YOLO_MODE": value})
            assert value == "0"
            return environment

        async def apply_default(self: Any, access: Any) -> None:
            connection, session_id = self._bound_session_or_not_applied()
            await self._guarded(
                connection.set_session_mode(session_id=session_id, mode_id="default")
            )

        original_set_mode = ClientSideConnection.set_session_mode

        async def observe_mode(
            self: ClientSideConnection, session_id: str, mode_id: str, **kwargs: Any
        ) -> Any:
            # This is downstream of _apply_access, so removing its override reveals
            # production's dont_ask request and fails independently of the action.
            assert mode_id == "default"
            response = await original_set_mode(self, session_id, mode_id, **kwargs)
            observations.append(
                {
                    "acp_mode_request": mode_id,
                    "acp_mode_accepted": True,
                    "acp_mode_response": response.model_dump(mode="json"),
                }
            )
            return response

        monkeypatch.setattr(ClientSideConnection, "set_session_mode", observe_mode)

        monkeypatch.setattr(hermes, "_child_environment", observe_environment)
        monkeypatch.setattr(hermes.HermesAcpBackendChild, "_apply_access", apply_default)


def assert_native_configuration(
    backend: ConversationBackendKey, observations: list[dict[str, Any]]
) -> None:
    """Fail closed even when a launch/mapping hook stopped being used."""
    assert observations, "no effective native configuration was observed"
    if backend is ConversationBackendKey.codex:
        assert any(row.get("method") == "thread/start" for row in observations)
        assert any(row.get("method") == "turn/start" for row in observations)
    elif backend is ConversationBackendKey.claude:
        assert any(row.get("cli_permission_mode") == "default" for row in observations)
    else:
        assert any(row.get("child_HERMES_YOLO_MODE") == "0" for row in observations)
        assert any(row.get("acp_mode_accepted") is True for row in observations)
