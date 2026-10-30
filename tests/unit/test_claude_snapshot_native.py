"""Native command composition reaches the actual SDK frame guard and durable replay."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from tests.unit.test_claude_settings_snapshot import ScriptedTransport
from tests.unit.test_destination_catalog_observer import launch_fixture
from tests.unit.test_executor_native_runtime import (
    _fixture_admission,
    canonical_snapshot,
)

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkBackendChildFactory,
    _ClaudeProtocolTransport,
    _ObservedClaudeSdkClient,
)
from planner.conversation.backends.contracts import BackendChildFactory, EmissionCheck, TurnToken
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor.codec import encode_value
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.runtime import NativeChildRegistry


@pytest.mark.parametrize("failure", ["ended", "cancel"])
def test_native_command_preserves_captured_turn_guard_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)
    async def run() -> None:
        actual: list[ScriptedTransport] = []

        def client_factory(options: ClaudeAgentOptions) -> _ObservedClaudeSdkClient:
            inner = ScriptedTransport(options)
            actual.append(inner)
            return _ObservedClaudeSdkClient(options, _ClaudeProtocolTransport(inner))

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            return ClaudeAgentSdkBackendChildFactory(launches.claude, client_factory=client_factory)

        reference = ChildReference(str(uuid4()), str(uuid4()), "native-claude")
        spool = FilesystemSpool(tmp_path / "spool")
        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            reference.conversation_id,
            ConversationBackendKey.claude,
            "opus",
            "high",
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_fixture"),)),
            tmp_path,
            ConversationAccess.full,
            WorkspacePathPolicy.native_absolute,
        )
        expectation = _fixture_admission(registry, reference, resolved)
        try:
            start = ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {
                    "resolved_start": encode_value(resolved),
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                    "admission_expectation": encode_value(expectation),
                },
            )
            assert (
                await registry.execute_command(start)
            ).disposition is CommandDisposition.succeeded
            turn = TurnToken(reference.conversation_id, 1)
            prompt = ChildCommand(
                "prompt",
                reference,
                ChildOperation.write_prompt,
                turn,
                {
                    "content": encode_value(text_message_content("synthetic")),
                    "sender_content": encode_value(text_message_content("synthetic")),
                    "sender_label": "Owner",
                    "mode": PromptDeliveryMode.queue.value,
                    "admission_expectation": encode_value(replace(expectation, turn=turn)),
                },
            )
            outcome = await registry.execute_command(prompt)
            assert outcome.disposition is CommandDisposition.succeeded
            assert outcome.result["provider_submission"] == {
                "provider": "claude", "status": "accepted",
                "observation_kind": "user_message_admitted",
                "correlation_id": next(f["uuid"] for f in actual[0].frames if f["type"] == "user"),
            }
            assert await registry.execute_command(prompt) == outcome
            assert len(actual) == 1
            assert len([f for f in actual[0].frames if f["type"] == "user"]) == 1
            bad_turn = ChildCommand(
                "wrong-turn",
                reference,
                ChildOperation.steer,
                turn,
                {
                    "content": encode_value(text_message_content("never emitted")),
                    "sender_label": "Owner",
                    "admission_expectation": encode_value(
                        replace(expectation, turn=TurnToken(reference.conversation_id, 2))
                    ),
                },
            )
            assert (
                await registry.execute_command(bad_turn)
            ).disposition is CommandDisposition.refused
            assert len([f for f in actual[0].frames if f["type"] == "user"]) == 1
            # Mutate the captured turn immediately before the actual frame guard binds.
            # This boundary proves native admission alone cannot replace that final guard.
            backend = cast(ClaudeAgentSdkBackendChild, registry.children[reference.child_id].native)
            original_bind = actual[0].bind_user_check

            def change_before_wire(identity: str, check: EmissionCheck) -> None:
                if failure == "ended":
                    backend._turn = None
                else:
                    assert backend._turn is not None
                    backend._turn.cancel_requested = True
                original_bind(identity, check)

            monkeypatch.setattr(actual[0], "bind_user_check", change_before_wire)
            steer = ChildCommand(
                "steer",
                reference,
                ChildOperation.steer,
                turn,
                {
                    "content": encode_value(text_message_content("never emitted")),
                    "sender_label": "Owner",
                    "admission_expectation": encode_value(replace(expectation, turn=turn)),
                },
            )
            refused = await registry.execute_command(steer)
            assert refused.disposition is CommandDisposition.succeeded
            assert refused.result["kind"] == "steer_refused"
            assert "synthetic-private-input" not in repr(refused)
            assert "changed-private-input" not in repr(refused)
            assert len([f for f in actual[0].frames if f["type"] == "user"]) == 1
            assert await registry.execute_command(steer) == refused
        finally:
            if actual:
                child = registry.children.get(reference.child_id)
                if child is not None:
                    await child.native.stop()
            spool.close()

    asyncio.run(run())
