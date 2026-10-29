"""Native command composition reaches the real SDK adapter and private scope receipt."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from tests.unit.test_claude_settings_snapshot import ScriptedTransport, bench
from tests.unit.test_executor_native_runtime import (
    _fixture_admission,
    _FixtureContextObserver,
    canonical_snapshot,
)

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
    _ClaudeProtocolTransport,
    _ObservedClaudeSdkClient,
)
from planner.conversation.backends.claude_settings import read_claude_catalog_settings_snapshot
from planner.conversation.backends.contracts import BackendChildFactory, TurnToken
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
from planner.conversation.model_resolution.contracts import SelectionKind
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


@pytest.mark.parametrize("failure", ["scope", "ended", "cancel"])
def test_native_command_reaches_exact_child_scope_guard_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    async def run() -> None:
        discovery, discovery_transport, _, scope_registry = bench()
        await discovery.connect()
        sample = await read_claude_catalog_settings_snapshot(discovery_transport)
        scope = scope_registry.retain(sample.settings)
        await discovery.disconnect()
        actual: list[ScriptedTransport] = []

        def client_factory(options: ClaudeAgentOptions) -> _ObservedClaudeSdkClient:
            inner = ScriptedTransport(options)
            actual.append(inner)
            return _ObservedClaudeSdkClient(
                options, _ClaudeProtocolTransport(inner), claude_scope_registry=scope_registry
            )

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            return ClaudeAgentSdkBackendChildFactory(
                ClaudeAgentSdkChildLaunch(),
                client_factory=client_factory,
                claude_scope_registry=scope_registry,
            )

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
        context = replace(expectation.context, provider_scope=scope)
        expectation = replace(
            expectation,
            context=context,
            evidence=replace(expectation.evidence, context=context),
            selection=replace(expectation.selection, selection_kind=SelectionKind.provider_alias),
            resolved_model_id="claude-opus-5",
        )
        registry.admission_observer = _FixtureContextObserver(context)
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
            assert len(actual) == 1
            assert len([f for f in actual[0].frames if f["type"] == "user"]) == 1
            assert not scope_registry._receipts
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
            # Inject drift after the steer starts its asynchronous metadata read.
            # Native command binding alone cannot see the provider turn end or cancel.
            if failure == "scope":
                actual[0].settings["effective"]["model"] = "changed-policy"
            else:
                backend = cast(
                    ClaudeAgentSdkBackendChild, registry.children[reference.child_id].native
                )
                original_send = actual[0].send
                changed = False

                async def interrupt_during_sample(data: str) -> None:
                    nonlocal changed
                    frame = json.loads(data)
                    if (
                        not changed
                        and frame.get("type") == "control_request"
                        and frame["request"]["subtype"] == "get_settings"
                    ):
                        changed = True
                        if failure == "ended":
                            backend._turn = None
                        else:
                            assert backend._turn is not None
                            backend._turn.cancel_requested = True
                    await original_send(data)

                monkeypatch.setattr(actual[0], "send", interrupt_during_sample)
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
            assert len([f for f in actual[0].frames if f["type"] == "user"]) == 1
            assert await registry.execute_command(steer) == refused
            assert not scope_registry._receipts
        finally:
            if actual:
                child = registry.children.get(reference.child_id)
                if child is not None:
                    await child.native.stop()
            spool.close()

    asyncio.run(run())
