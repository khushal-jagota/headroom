"""Native provider receipts and replay through the remote child interface."""

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
        ConversationRoleMaterials,
        PromptDeliveryMode,
        ResolvedConversationStart,
    )
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
    from planner.execution_identity import ChildReference, ExecutorIdentity
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.mac_connection import MacConnection
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
            connection=MacConnection(
                ExecutorIdentity(
                    PROTOCOL_VERSION,
                    "a" * 40,
                    SPOOL_FORMAT_VERSION,
                    reference.executor_incarnation,
                    1,
                )
            ),
            outbox=spool,
            factory=cast(Any, lambda *args: lambda **kwargs: native),
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        commands: list[ChildCommand] = []

        async def send(command: ChildCommand) -> Any:
            commands.append(command)
            return await registry.run(command)

        remote = MacRemoteChild(reference, send, lambda _: canonical_snapshot())
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
            recorded = registry.ask((command.command_id,))[0].outcome
            assert recorded is not None
            expected = {
                "accepted": CommandDisposition.succeeded,
                "rejected": CommandDisposition.refused,
                "unknown": CommandDisposition.uncertain,
            }[fate]
            assert recorded.disposition is expected
            assert await registry.run(command) == recorded
            assert writes == [turn]
        finally:
            spool.close()

    asyncio.run(run())
