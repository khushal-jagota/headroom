"""Final transport guards prevent stale writes; native outcomes preserve replay.

The lock cases need the pinned provider transport to prove when a guard runs.
Native accepted, rejected and uncertain outcomes cross the real remote codec.
All provider and transport effects are synthetic.
"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.backends.claude_agent_sdk import _ClaudeCleanupTransport
from planner.conversation.backends.codex_app_server.client import CodexAppServerClient
from planner.conversation.backends.contracts import AdmissionRefused
from planner.conversation.contracts import (
    ConversationBackendKey,
    WorkspacePathPolicy,
)
from planner.conversation.model_resolution.contracts import (
    CatalogWorkspaceBinding,
    ModelSelection,
    SelectionKind,
)
from planner.tickets.contracts import TicketLocale


def expectation(workspace: Path) -> AdmissionExpectation:
    return AdmissionExpectation(
        selection=ModelSelection(
            TicketLocale.macos,
            ConversationBackendKey.codex,
            "exact",
            SelectionKind.concrete,
            None,
            "1",
        ),
        workspace=CatalogWorkspaceBinding(workspace, WorkspacePathPolicy.native_absolute, None),
        context=None,
        evidence=None,
        attempt_id="attempt",
        launch_token="exact",
        resolved_model_id=None,
        disabled_models_revision="[]",
        conversation_id="conversation",
        reasoning_effort=None,
    )


def test_codex_guard_runs_after_owned_write_lock_and_cleans_pending_request() -> None:
    async def run() -> None:
        writes: list[bytes] = []

        async def drain() -> None:
            pass

        client = CodexAppServerClient(handler=cast(Any, None), description="admission")
        client._process = cast(
            Any, SimpleNamespace(stdin=SimpleNamespace(write=writes.append, drain=drain))
        )
        valid = True

        def check(prepared: object = None) -> None:
            del prepared
            if not valid:
                raise AdmissionRefused("context_changed")

        await client._write_lock.acquire()
        pending = asyncio.create_task(
            client.begin_request("turn/start", {"model": "exact"}, emission_check=check)
        )
        await asyncio.sleep(0)
        valid = False
        client._write_lock.release()
        with pytest.raises(AdmissionRefused, match="context_changed"):
            await pending
        assert writes == []
        assert client._pending == {}

    asyncio.run(run())


def test_claude_message_guard_survives_scope_exit_and_waits_for_sdk_lock() -> None:
    async def run() -> None:
        writes: list[str] = []

        async def send(data: str) -> None:
            writes.append(data)

        transport = _ClaudeCleanupTransport("", ClaudeAgentOptions())
        transport._ready = True
        transport._stdin_stream = cast(Any, SimpleNamespace(send=send))
        valid = True

        def check(prepared: object = None) -> None:
            del prepared
            if not valid:
                raise AdmissionRefused("context_changed")

        transport.bind_user_check("exact-message", check)
        await transport._write_lock.acquire()
        # No callback is drawn from this background task's ContextVar.
        pending = asyncio.create_task(
            transport.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": "exact-message",
                        "message": {"content": "exact"},
                    }
                )
            )
        )
        await asyncio.sleep(0)
        valid = False
        transport._write_lock.release()
        with pytest.raises(AdmissionRefused, match="context_changed"):
            await pending
        assert writes == []
        # An unrelated control response is not this user message and consumes no guard.
        await transport.write(json.dumps({"type": "control_response"}))
        assert len(writes) == 1

    asyncio.run(run())


@pytest.mark.parametrize("fate", ["accepted", "rejected", "unknown"])
def test_native_provider_outcomes_preserve_receipts_uncertainty_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fate: str,
) -> None:
    """The real native codec and remote decoder must agree before ownership can settle."""
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.conversation.backends.contracts import (
        BackendPromptAccepted,
        BackendPromptRejected,
        PromptWriteFailed,
        RemoteCommandUncertain,
        TurnToken,
    )
    from planner.conversation.contracts import (
        ConversationAccess,
        PromptDeliveryMode,
        ResolvedConversationStart,
    )
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.execution_identity import ChildReference
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.mac_runner import MacRunner
    from planner.executor.protocol import ChildCommand, CommandDisposition
    from planner.executor.vps_remote_child import MacRemoteChild

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "provider-home"))

    async def run() -> None:
        reference = ChildReference("11111111-1111-4111-8111-111111111111", "native", "conversation")
        turn = TurnToken("conversation", 1)
        writes: list[TurnToken] = []

        class Native:
            async def start(self, *args: object, **kwargs: object) -> None:
                pass

            async def write_prompt(
                self, token: TurnToken, *args: object, **kwargs: object
            ) -> BackendPromptAccepted:
                writes.append(token)
                if fate == "unknown":
                    raise PromptWriteFailed("acknowledgment missing after possible write")
                if fate == "rejected":
                    raise BackendPromptRejected("provider refused request")
                return BackendPromptAccepted(True)

        native = Native()
        spool = MacOutbox(tmp_path / "spool")
        registry = MacRunner(
            paths=ExecutorPaths(tmp_path / "native"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=cast(Any, lambda *args: lambda **kwargs: native),
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        commands: list[ChildCommand] = []

        async def send(command: ChildCommand) -> Any:
            commands.append(command)
            return await registry.run(command)

        remote = MacRemoteChild(reference, send, lambda _: canonical_snapshot())
        exact = replace(expectation(tmp_path), context=None, evidence=None, resolved_model_id=None)
        resolved = ResolvedConversationStart(
            "conversation",
            ConversationBackendKey.codex,
            "exact",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
            workspace_path_policy=WorkspacePathPolicy.native_absolute,
        )
        try:
            await remote.start(resolved, vendor_session_cursor=None)
            exact = replace(exact, turn=turn)
            content = text_message_content("literal intent without catalog")

            async def write() -> BackendPromptAccepted:
                return await remote.write_prompt(
                    turn,
                    content,
                    sender_content=content,
                    sender_label="Owner",
                    mode=PromptDeliveryMode.queue,
                    model_change="exact",
                    reasoning_effort_change=None,
                    admission_expectation=exact,
                )

            if fate == "unknown":
                with pytest.raises(PromptWriteFailed) as uncertain:
                    await write()
                assert isinstance(uncertain.value, RemoteCommandUncertain)
            elif fate == "rejected":
                with pytest.raises(BackendPromptRejected, match="provider refused request"):
                    await write()
            else:
                accepted = await write()
                assert accepted.composed_content_delivered is True
            command = commands[-1]
            recorded = registry._commands[command.command_id].outcome
            assert recorded is not None
            expected = {
                "accepted": CommandDisposition.succeeded,
                "rejected": CommandDisposition.refused,
                "unknown": CommandDisposition.uncertain,
            }[fate]
            assert recorded.disposition is expected
            assert await registry.run(command) == recorded
            assert writes == [turn]
            assert registry._children[reference.child_id].active_turn == (
                None if fate == "rejected" else turn
            )
        finally:
            spool.close()

    asyncio.run(run())
