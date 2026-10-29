"""Native command composition reaches the real SDK adapter and private scope receipt."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from tests.unit.test_claude_settings_snapshot import ScriptedTransport, bench
from tests.unit.test_destination_catalog_observer import ScriptedLaunchEnvironment, launch_fixture
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
from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
from planner.conversation.backends.claude_settings import (
    ClaudeSettingsScopeRegistry,
    read_claude_catalog_settings_snapshot,
)
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
from planner.conversation.model_resolution import observer
from planner.conversation.model_resolution.contracts import CatalogObservation, SelectionKind
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
from planner.tickets.contracts import TicketLocale


@pytest.mark.parametrize("failure", ["scope", "ended", "cancel", "input"])
def test_native_command_reaches_exact_child_scope_guard_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)
    selected = tmp_path / "claude-home" / "settings.json"
    selected.parent.mkdir()
    selected.write_text('{"unknown":{"private":"synthetic-private-input"}}')
    original_document = selected.read_bytes()

    async def run() -> None:
        discovery, discovery_transport, _, scope_registry = bench()
        await discovery.connect()
        sample = await read_claude_catalog_settings_snapshot(discovery_transport)
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
                launches.claude,
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
        async def catalog(
            executable: str, *, environment: Mapping[str, str] | None = None,
            working_directory: Path | None = None,
            claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
        ) -> ClaudeModelCatalog:
            del executable, environment, working_directory
            assert claude_scope_registry is scope_registry
            return ClaudeModelCatalog(
                (ClaudeModel("opus", "Opus", "claude-opus-5", ("high",)),), (),
                provider_scope=scope_registry.retain(sample.settings),
            )

        monkeypatch.setattr(observer, "probe_claude_model_catalog", catalog)
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.macos,
            executor_identity=expectation.context.executor_identity,
            panels_server_url="http://fixture.invalid", launches=lambda: launches,
            environment=ScriptedLaunchEnvironment(), claude_scope_registry=scope_registry,
        )
        input_basis = await reader.capture_context(
            ConversationBackendKey.claude, workspace=expectation.workspace
        )
        observation = await reader.observe(
            ConversationBackendKey.claude, workspace=expectation.workspace,
            attempt_id=expectation.attempt_id,
            deadline=asyncio.get_running_loop().time() + 10,
        )
        assert isinstance(observation, CatalogObservation)
        context = observation.evidence.context
        after = await reader.capture_context(
            ConversationBackendKey.claude, workspace=expectation.workspace
        )
        assert reader.verify_observation_basis(
            input_basis, after, observation, attempt_id=expectation.attempt_id,
        )
        if failure == "input":
            assert not reader.verify_context(input_basis)
            assert not reader.verify_observation_basis(
                input_basis, after, observation, attempt_id="other-attempt",
            )
            forged = replace(
                observation, evidence=replace(observation.evidence, observation_id="forged"),
            )
            assert not reader.verify_observation_basis(
                input_basis, after, forged, attempt_id=expectation.attempt_id,
            )
            for identity in (
                context.executor_identity,
                replace(context.executor_identity, executor_incarnation=str(uuid4()))
                if context.executor_identity is not None else None,
            ):
                fresh = observer.ProductionCatalogObserver(
                    destination=TicketLocale.macos, executor_identity=identity,
                    panels_server_url="http://fixture.invalid", launches=lambda: launches,
                    environment=ScriptedLaunchEnvironment(), claude_scope_registry=scope_registry,
                )
                assert not fresh.verify_context(context)
        expectation = replace(
            expectation,
            context=context,
            evidence=observation.evidence,
            selection=replace(expectation.selection, selection_kind=SelectionKind.provider_alias),
            resolved_model_id="claude-opus-5",
        )
        registry.admission_observer = reader
        assert reader.verify_context(context)
        assert not reader.verify_context(replace(context, context_ref="forged"))
        # Replace the selected document after discovery, before actual native admission.
        replacement = selected.with_name("replacement")
        replacement.write_bytes(selected.read_bytes())
        replacement.replace(selected)
        assert reader.verify_context(context)
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
                        if failure == "input":
                            # This occurs after NativeChildRegistry's admission check,
                            # during the actual-child sample before the SDK frame guard.
                            selected.write_text('{"unknown":{"private":"changed-private-input"}}')
                        elif failure == "ended":
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
            assert "synthetic-private-input" not in repr(refused)
            assert "changed-private-input" not in repr(refused)
            assert len([f for f in actual[0].frames if f["type"] == "user"]) == 1
            assert await registry.execute_command(steer) == refused
            assert not scope_registry._receipts
            if failure == "input":
                changed_basis = await reader.capture_context(
                    ConversationBackendKey.claude, workspace=expectation.workspace,
                )
                assert changed_basis.context_ref != input_basis.context_ref
                selected.write_bytes(original_document)
                restored_basis = await reader.capture_context(
                    ConversationBackendKey.claude, workspace=expectation.workspace,
                )
                assert restored_basis.context_ref not in {
                    input_basis.context_ref, changed_basis.context_ref,
                }
                # A captured A-to-B-to-A transition never revives old finalization.
                assert not reader.verify_context(context)
        finally:
            if actual:
                child = registry.children.get(reference.child_id)
                if child is not None:
                    await child.native.stop()
            spool.close()

    asyncio.run(run())
