"""The real exercise must fail if a test override silently stops applying."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from acp.client.connection import ClientSideConnection
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from tests.support.real_permission_mapping import (
    assert_native_configuration,
    install_permission_mapping,
)

from planner.conversation.backends.codex_app_server import adapter as codex
from planner.conversation.backends.codex_app_server.client import CodexAppServerClient
from planner.conversation.contracts import ConversationAccess, ConversationBackendKey


@pytest.mark.parametrize("backend", list(ConversationBackendKey))
def test_missing_native_configuration_observation_fails(backend: ConversationBackendKey) -> None:
    with pytest.raises(AssertionError):
        assert_native_configuration(backend, [])


def test_codex_observer_rejects_production_full_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_wire(*args: Any, **kwargs: Any) -> Any:
        return None

    # Isolate the network, but leave the observer and generated native params real.
    original_policy = codex._approval_policy
    monkeypatch.setattr(CodexAppServerClient, "begin_request", no_wire)
    observations: list[dict[str, Any]] = []
    install_permission_mapping(monkeypatch, ConversationBackendKey.codex, observations)
    monkeypatch.setattr(codex, "_approval_policy", original_policy)
    with pytest.raises(AssertionError):
        asyncio.run(
            CodexAppServerClient.begin_request(
                cast(Any, None),
                "thread/start",
                {
                    "approvalPolicy": codex._approval_policy(ConversationAccess.full),
                    "approvalsReviewer": codex._approvals_reviewer(ConversationAccess.full),
                    "sandbox": codex._thread_sandbox(ConversationAccess.full),
                },
            )
        )
    assert observations[0]["approvalPolicy"] == "never"


def test_claude_observer_rejects_production_bypass_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    observations: list[dict[str, Any]] = []
    install_permission_mapping(monkeypatch, ConversationBackendKey.claude, observations)
    transport = SubprocessCLITransport(
        prompt="", options=ClaudeAgentOptions(permission_mode="bypassPermissions")
    )
    transport._cli_path = "/unused/native-claude"
    with pytest.raises(AssertionError):
        transport._build_command()
    assert observations == [{"cli_permission_mode": "bypassPermissions"}]


def test_hermes_observer_rejects_production_dont_ask_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    observations: list[dict[str, Any]] = []
    install_permission_mapping(monkeypatch, ConversationBackendKey.hermes, observations)
    with pytest.raises(AssertionError):
        asyncio.run(ClientSideConnection.set_session_mode(cast(Any, None), "session", "dont_ask"))
    assert observations == []


def test_completion_between_view_and_event_reads_is_observed() -> None:
    import httpx
    from tests.integration.test_conversation_real_provider_permissions import _poll

    view_reads = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal view_reads
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"events": [{"kind": "turn_ended"}]})
        view_reads += 1
        return httpx.Response(200, json={"is_running": view_reads == 1})

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://test"
        ) as client:
            receipts: dict[str, Any] = {}
            view = await _poll(
                client, "/conversation", lambda v: not v["is_running"], lambda: None, receipts
            )
            assert view["is_running"] is False
            assert receipts["last_view"] == view

    asyncio.run(exercise())
    assert view_reads == 2
