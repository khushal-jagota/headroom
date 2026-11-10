"""Native provider receipts and replay through the remote child interface.

A Mac prompt is accepted when its mailbox row commits. What the provider then did is the
Mac's recorded outcome, and a prompt it did not deliver ends its turn failed through the
outbox (R1), whose fixed identity a replay does not add again."""

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from planner.conversation.contracts import (
    ConversationBackendKey,
    WorkspacePathPolicy,
)


@pytest.mark.parametrize("fate", ["accepted", "rejected", "unknown"])
def test_native_provider_outcomes_preserve_receipts_uncertainty_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fate: str,
) -> None:
    """The real native codec and the Mac's outcome must agree before ownership settles."""
    from tests.support.executor_turns import scripted_remote_child
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.conversation.backends.contracts import (
        BackendPromptAccepted,
        BackendPromptRejected,
        PromptWriteFailed,
        TurnEnded,
        TurnToken,
    )
    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationRoleMaterials,
        PromptDeliveryMode,
        ResolvedConversationStart,
    )
    from planner.conversation.events import ConversationTurnEnding
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.execution_identity import ChildReference
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.mac_runner import MacRunner
    from planner.executor.protocol import (
        BackendEvent,
        ChildCommand,
        CommandDisposition,
        EnvelopeKind,
        decode_event_arguments,
        type_adapter,
    )

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
            outbox=spool,
            factory=cast(Any, lambda *args: lambda **kwargs: native),
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        commands: list[ChildCommand] = []

        async def send(command: ChildCommand) -> Any:
            commands.append(command)
            return await registry.run(command)

        remote = scripted_remote_child(reference, send, lambda _: canonical_snapshot())
        resolved = ResolvedConversationStart(
            "conversation",
            ConversationBackendKey.codex,
            "exact",
            None,
            ConversationRoleMaterials(
                "worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_provider"))
            ),
            tmp_path,
            ConversationAccess.full,
            workspace_path_policy=WorkspacePathPolicy.absolute,
        )
        try:
            await remote.start(resolved, vendor_session_cursor=None)
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
                )

            # Every fate is accepted at commit: the provider's answer comes later.
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

            def turn_endings() -> list[TurnEnded]:
                endings = []
                for envelope in spool.pending():
                    assert envelope.kind is EnvelopeKind.backend_event
                    event = type_adapter(BackendEvent).validate_python(envelope.payload)
                    assert (event.child, event.turn) == (reference, turn)
                    value = decode_event_arguments(event)
                    assert isinstance(value, TurnEnded)
                    endings.append(value)
                return endings

            published = turn_endings()
            if fate == "accepted":
                assert published == []
            else:
                (ending,) = published
                assert ending.ending is ConversationTurnEnding.failed
                assert ending.error_summary is not None
                assert (
                    "provider refused request"
                    if fate == "rejected"
                    else "it may have reached the agent"
                ) in ending.error_summary
            # A replay returns the recorded outcome and neither writes nor publishes again.
            assert await registry.run(command) == recorded
            assert turn_endings() == published
            assert writes == [turn]
        finally:
            spool.close()

    asyncio.run(run())
