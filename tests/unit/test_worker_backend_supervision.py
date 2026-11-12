"""Retained child custody must protect its scratch before backend preparation."""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from tests.support.conversation_codex_app_server_bench import _resolved_start

from planner.conversation import worker_scratch
from planner.conversation.backends.claude_agent_sdk import _ClaudeCleanupTransport
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexChildWouldNotStart,
)
from planner.conversation.backends.hermes_acp import AcpChildLaunch, HermesAcpBackendChild
from planner.conversation.contracts import ConversationRoleMaterials
from planner.conversation.message_files import ConversationMessageFiles
from planner.worker_supervision import WorkerCleanupError, WorkerSupervisor


@pytest.mark.parametrize("backend", ["codex", "claude", "hermes"])
def test_retained_custody_refuses_before_scratch_prunes_the_old_child(
    backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs"
    old = root / "ticket-abcdef"
    old.mkdir(parents=True)
    evidence = old / "still-needed"
    evidence.write_text("old child still owns this scratch")
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: root)
    monkeypatch.setattr(WorkerSupervisor, "create", AsyncMock(
        side_effect=WorkerCleanupError("Ticket retains unproven cleanup")
    ))
    for module in ("codex_app_server.client", "claude_agent_sdk"):
        monkeypatch.setattr(
            f"planner.conversation.backends.{module}.ensure_cli_compatible", AsyncMock()
        )
    start = replace(_resolved_start(tmp_path), role_materials=ConversationRoleMaterials(
        role_text="Worker", identity_environment_variables=(("PLAN_TICKET_ID", "ticket"),),
    ))

    async def exercise() -> None:
        if backend == "codex":
            client = CodexAppServerClient(handler=Mock(), description="retained")
            await client.start(
                argv=(sys.executable,), environment={"PLAN_TICKET_ID": "ticket"},
                working_directory=tmp_path, worker_ticket_id="ticket",
            )
        elif backend == "claude":
            transport = _ClaudeCleanupTransport("", ClaudeAgentOptions(
                cli_path=sys.executable, cwd=str(tmp_path), env={"PLAN_TICKET_ID": "ticket"},
            ))
            await transport.connect()
        else:
            child = HermesAcpBackendChild(
                launch=AcpChildLaunch((sys.executable,)), resolved_start=start,
                event_sink=Mock(), message_files=ConversationMessageFiles(tmp_path / "db"),
            )
            await child.start(start, vendor_session_cursor=None)

    # Codex converts an admission refusal to its established spawn-failure type.
    with pytest.raises((WorkerCleanupError, CodexChildWouldNotStart),
                       match="Ticket retains unproven cleanup"):
        asyncio.run(exercise())
    assert evidence.read_text() == "old child still owns this scratch"
    assert list(root.iterdir()) == [old]
