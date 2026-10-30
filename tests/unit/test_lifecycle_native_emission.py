"""Final owned transport waits cannot turn old observations into provider writes.

Risks absent from scope-1 metadata coverage: mutation during a write lock wait,
an SDK task with unrelated context, and malformed nested command identity.
No browser, server, subprocess, or provider is needed for these boundaries.
"""

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.admission.service import check_expectation, parse_expectation
from planner.conversation.backends.claude_agent_sdk import _ClaudeCleanupTransport
from planner.conversation.backends.codex_app_server.client import CodexAppServerClient
from planner.conversation.backends.contracts import AdmissionRefused
from planner.conversation.contracts import (
    ConversationBackendKey,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.model_resolution.contracts import (
    CatalogEvidenceReference,
    CatalogWorkspaceBinding,
    LaunchContext,
    ModelSelection,
    SelectionKind,
)
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    ExecutorIdentity,
    OutboxEntry,
    StartRun,
    Steer,
    WritePrompt,
    decode_command_arguments,
)
from planner.tickets.contracts import TicketLocale


def expectation(workspace: Path) -> AdmissionExpectation:
    binding = CatalogWorkspaceBinding(workspace, WorkspacePathPolicy.native_absolute, None)
    context = LaunchContext(
        "context",
        TicketLocale.macos,
        ConversationBackendKey.codex,
        ExecutorIdentity(
            PROTOCOL_VERSION,
            "a" * 40,
            SPOOL_FORMAT_VERSION,
            "11111111-1111-4111-8111-111111111111",
            1,
        ),
        binding,
        workspace,
        "binary",
        "1",
        "scope",
        None,
    )

    return AdmissionExpectation(
        ModelSelection(
            TicketLocale.macos,
            ConversationBackendKey.codex,
            "exact",
            SelectionKind.concrete,
            None,
            "1",
        ),
        binding,
        context,
        CatalogEvidenceReference(
            "observation", "revision", context, int(time.time()) - 1, "attempt", 0.0
        ),
        "attempt",
        "exact",
        None,
        "[]",
        "conversation",
        "exact",
        executor_identity=context.executor_identity,
    )


def resolved_for(exact: AdmissionExpectation, workspace: Path) -> ResolvedConversationStart:
    from planner.conversation.contracts import ConversationAccess

    return ResolvedConversationStart(
        exact.conversation_id,
        exact.selection.backend_key,
        exact.launch_token,
        exact.reasoning_effort,
        None,
        workspace,
        ConversationAccess.full,
        workspace_path_policy=exact.workspace.workspace_path_policy,
    )


def test_retained_initial_commands_and_stop_proofs_survive_disk_reopen(
    tmp_path: Path,
) -> None:
    """Retained initial commands and ordered stop output survive their actual stores.

    Provider results stay in memory; reopening output never invokes the provider.
    """
    from pydantic import TypeAdapter

    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationRoleMaterials,
        ResolvedConversationStart,
    )
    from planner.conversation.storage import ConversationStore
    from planner.core.db import connect, create_schema
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import (
        AcceptanceReceipt,
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
        CommandOutcome,
        EnvelopeKind,
        JsonObject,
        ReceiptDisposition,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.mac import MacExecutor
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.wire import ProtocolRefused, parse_identity

    def payload_object(value: object) -> JsonObject:
        payload = encode_value(value)
        assert isinstance(payload, dict)
        return payload

    async def prove() -> None:
        exact = expectation(tmp_path)
        assert exact.context is not None
        assert exact.context.executor_identity is not None
        child = ChildReference(
            exact.context.executor_identity.executor_incarnation, "old-child", "conversation"
        )
        resolved = ResolvedConversationStart(
            child.conversation_id,
            ConversationBackendKey.codex,
            "exact",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "ticket"),)),
            tmp_path,
            ConversationAccess.full,
        )
        old = ChildCommand(
            "old-pending",
            child,
            ChildOperation.start,
            None,
            decode_command_arguments(
                ChildOperation.start,
                {
                    "resolved_start": encode_value(resolved),
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                },
            ),
        )
        assert isinstance(old.arguments, StartRun)
        new = replace(
            old,
            command_id="new-pending",
            arguments=replace(old.arguments, admission_expectation=replace(exact, child=child)),
        )
        database = str(tmp_path / "core.db")
        conn = connect(database)
        try:
            create_schema(conn)
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
                "access,created_at) VALUES('conversation','codex','exact',?,'full',1)",
                (str(tmp_path),),
            )
            runtime = {"initial_commands": [encode_value(old), encode_value(new)]}
            ConversationStore.save_executor_turn_in_transaction(
                conn,
                child.conversation_id,
                (
                    json.dumps(encode_value(child)),
                    json.dumps({"conversation_id": child.conversation_id, "turn_number": 1}),
                    json.dumps(runtime),
                ),
            )
        finally:
            conn.close()
        reopened = connect(database)
        try:
            stored = json.loads(
                reopened.execute("SELECT runtime_json FROM executor_core_turns").fetchone()[0]
            )
            restored = tuple(
                TypeAdapter(ChildCommand).validate_python(raw) for raw in stored["initial_commands"]
            )
            assert restored == (old, new)
            assert isinstance(restored[0].arguments, (StartRun, WritePrompt, Steer))
            assert restored[0].arguments.admission_expectation is None
            assert isinstance(restored[1].arguments, (StartRun, WritePrompt, Steer))
            assert restored[1].arguments.admission_expectation == replace(exact, child=child)
        finally:
            reopened.close()

        paths = ExecutorPaths(tmp_path / "native")
        spool = MacOutbox(paths.spool)
        outcomes = (
            CommandOutcome("first-stop", child, CommandDisposition.succeeded, {}),
            CommandOutcome("later-stop", child, CommandDisposition.succeeded, {}),
        )
        envelopes = tuple(
            spool.add(
                OutboxEntry(
                    conversation_id=child.conversation_id,
                    kind=EnvelopeKind.child_stopped,
                    payload=payload_object(value),
                )
            )
            for value in outcomes
        )
        sender = spool.sender_id
        before = spool.pending()
        spool.close()
        executor = MacExecutor(
            paths=paths,
            revision="a" * 40,
            executor_id="fixture-only",
            panels_server_url="https://unused.invalid",
        )
        try:
            assert executor.spool.sender_id == sender
            assert executor.spool.pending() == before
            assert executor.spool.pending() == envelopes
            assert executor.identity.executor_incarnation != child.executor_incarnation

            def never_create(*args: object, **kwargs: object) -> Any:
                pytest.fail("persisted command replay reached provider factory")

            executor.registry.factory = never_create
            assert executor.registry.children == {} and executor.registry.outcomes == {}
            old_incarnation = await executor.registry.execute_command(old)
            assert old_incarnation.disposition is CommandDisposition.refused
            assert old_incarnation.result == {"reason": "incarnation_gone"}
            legacy_current = replace(
                old,
                command_id="legacy-proven-nondispatch",
                child=replace(child, executor_incarnation=executor.identity.executor_incarnation),
            )
            legacy_refusal = await executor.registry.execute_command(legacy_current)
            assert legacy_refusal.disposition is CommandDisposition.refused
            assert legacy_refusal.result["reason"] == "legacy_unscoped"

            submitted: list[dict[str, Any]] = []
            executor._handshake_accepted = True

            async def request(kind: WireMessageKind, payload: dict[str, Any]) -> WireMessage:
                assert kind is WireMessageKind.envelope
                submitted.append(payload)
                receipt = AcceptanceReceipt(
                    payload["sender_id"],
                    payload["submission_id"],
                    payload["payload_sha256"],
                    ReceiptDisposition.accepted,
                    {},
                    1,
                )
                return WireMessage(WireMessageKind.receipt, "fixture", payload_object(receipt))

            executor._request = request  # type: ignore[method-assign]
            sender_task = asyncio.create_task(executor.spool.send_pending(executor))
            try:
                async with asyncio.timeout(2):
                    while executor.spool.pending_count():
                        await asyncio.sleep(0.001)
            finally:
                sender_task.cancel()
                await asyncio.gather(sender_task, return_exceptions=True)
            original = {entry.submission_id: encode_value(entry) for entry in envelopes}
            replayed_original = [entry for entry in submitted if entry["submission_id"] in original]
            assert len(replayed_original) == 2
            assert all(entry == original[entry["submission_id"]] for entry in replayed_original)
            assert executor.spool.pending_count() == 0 and not executor.registry.children
            assert PROTOCOL_VERSION == 15 and SPOOL_FORMAT_VERSION == 4
            with pytest.raises(ProtocolRefused):
                parse_identity(
                    encode_value(
                        replace(executor.identity, protocol_version=13, connection_generation=1)
                    )
                )
            assert (
                parse_identity(
                    encode_value(replace(executor.identity, connection_generation=1))
                ).protocol_version
                == 15
            )
        finally:
            executor.spool.close()

    asyncio.run(prove())


def test_native_expectation_rejects_malformed_identity_without_catalog_gate(tmp_path: Path) -> None:
    exact = expectation(tmp_path)
    raw = cast(dict[str, Any], encode_value(exact))
    raw["executor_identity"]["connection_generation"] = True
    with pytest.raises(ValueError, match="identity integer"):
        parse_expectation(raw)
    # Catalog age, account observation and concrete resolution are advisory.
    check_expectation(replace(exact, context=None, evidence=None, resolved_model_id=None))


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


def test_legacy_ticket_command_refuses_before_factory_and_replays_same_outcome(
    tmp_path: Path,
) -> None:
    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationRoleMaterials,
        ResolvedConversationStart,
    )
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
    )
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.runtime import NativeChildRegistry

    async def run() -> None:
        def never_create(*args: object, **kwargs: object) -> Any:
            pytest.fail("legacy command reached provider factory")

        reference = ChildReference("11111111-1111-4111-8111-111111111111", "child", "conversation")
        registry = NativeChildRegistry(
            incarnation="11111111-1111-4111-8111-111111111111",
            spool=MacOutbox(tmp_path / "spool"),
            factory=never_create,
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        resolved = ResolvedConversationStart(
            "conversation",
            ConversationBackendKey.codex,
            "exact",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "ticket"),)),
            tmp_path,
            ConversationAccess.full,
        )
        command = ChildCommand(
            "legacy",
            reference,
            ChildOperation.start,
            None,
            decode_command_arguments(
                ChildOperation.start,
                {
                    "resolved_start": encode_value(resolved),
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                },
            ),
        )
        first = await registry.execute_command(command)
        assert first.disposition is CommandDisposition.refused
        assert first.result["reason"] == "legacy_unscoped"
        assert first.result["corrective_action"] == "classify_selection"
        assert await registry.execute_command(command) == first
        assert registry.children == {}

    asyncio.run(run())


def test_final_refusal_leaves_retained_native_child_without_a_new_turn(tmp_path: Path) -> None:
    from planner.conversation.backends.contracts import TurnToken
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
    )
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.runtime import NativeChildRegistry, _Child

    async def run() -> None:
        prepared = False

        class Native:
            async def write_prompt(self, *args: object, **kwargs: object) -> None:
                nonlocal prepared
                prepared = True
                registry._retiring_children.add(reference.child_id)
                check = kwargs.get("emission_check")
                assert callable(check)
                check()
                pytest.fail("failed final guard submitted provider bytes")

        exact = expectation(tmp_path)
        reference = ChildReference("11111111-1111-4111-8111-111111111111", "child", "conversation")
        turn = TurnToken("conversation", 1)
        exact = replace(exact, child=reference, turn=turn)
        registry = NativeChildRegistry(
            incarnation="11111111-1111-4111-8111-111111111111",
            spool=MacOutbox(tmp_path / "spool"),
            factory=cast(Any, None),
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        child = _Child(
            reference,
            "capability",
            cast(Any, Native()),
            ticket_id="ticket",
            launch_input=StartRun(
                resolved_start=resolved_for(exact, tmp_path),
                canonical_skill_snapshot=canonical_snapshot(),
            ),
            localized_start=resolved_for(exact, tmp_path),
            model="exact",
            backend_key=ConversationBackendKey.codex,
        )
        registry.children[reference.child_id] = child
        content = encode_value(text_message_content("exact message"))
        command = ChildCommand(
            "prompt",
            reference,
            ChildOperation.write_prompt,
            turn,
            decode_command_arguments(
                ChildOperation.write_prompt,
                {
                    "admission_expectation": encode_value(exact),
                    "content": content,
                    "sender_content": content,
                    "sender_label": "Owner",
                    "mode": "queue",
                },
            ),
        )
        outcome = await registry.execute_command(command)
        assert outcome.disposition is CommandDisposition.refused
        assert outcome.result["reason"] == "context_changed"
        assert child.active_turn is None
        assert prepared

    asyncio.run(run())


def test_claude_advertised_alias_change_does_not_rebind_native_prompt(tmp_path: Path) -> None:
    from planner.conversation.backends.contracts import BackendPromptAccepted, TurnToken
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
    )
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.runtime import NativeChildRegistry, _Child

    async def run() -> None:
        base = expectation(tmp_path)
        assert base.context is not None
        context = replace(base.context, backend_key=ConversationBackendKey.claude)
        reference = ChildReference("11111111-1111-4111-8111-111111111111", "child", "conversation")
        turn = TurnToken("conversation", 1)
        exact = replace(
            base,
            context=context,
            evidence=None,
            selection=replace(
                base.selection,
                backend_key=ConversationBackendKey.claude,
                submitted_token="alias",
                selection_kind=SelectionKind.provider_alias,
            ),
            child=reference,
            turn=turn,
            launch_token="alias",
            resolved_model_id=None,
        )
        writes: list[object] = []

        class Native:
            async def write_prompt(self, *args: object, **kwargs: object) -> BackendPromptAccepted:
                writes.append(kwargs.get("model_change"))
                return BackendPromptAccepted(True)

        registry = NativeChildRegistry(
            incarnation=reference.executor_incarnation,
            spool=MacOutbox(tmp_path / "spool"),
            factory=cast(Any, None),
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        child = _Child(
            reference,
            "capability",
            cast(Any, Native()),
            ticket_id="ticket",
            launch_input=StartRun(
                resolved_start=resolved_for(exact, tmp_path),
                canonical_skill_snapshot=canonical_snapshot(),
            ),
            localized_start=resolved_for(exact, tmp_path),
            model="alias",
            backend_key=ConversationBackendKey.claude,
        )
        registry.children[reference.child_id] = child
        content = encode_value(text_message_content("exact request"))
        command = ChildCommand(
            "prompt",
            reference,
            ChildOperation.write_prompt,
            turn,
            decode_command_arguments(
                ChildOperation.write_prompt,
                {
                    "admission_expectation": encode_value(exact),
                    "content": content,
                    "sender_content": content,
                    "sender_label": "Owner",
                    "mode": "queue",
                    "model_change": "alias",
                },
            ),
        )
        outcome = await registry.execute_command(command)
        assert outcome.disposition is CommandDisposition.succeeded, outcome.result
        assert writes == ["alias"]
        assert await registry.execute_command(command) == outcome
        assert writes == ["alias"]
        cast(MacOutbox, registry.spool).close()

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
        TurnToken,
    )
    from planner.conversation.contracts import (
        ConversationAccess,
        PromptDeliveryMode,
        ResolvedConversationStart,
    )
    from planner.conversation.events import ProviderSubmissionFact
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import ChildCommand, ChildReference, CommandDisposition
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.remote import RemoteBackendChild, RemoteCommandUncertain
    from planner.executor.runtime import NativeChildRegistry

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "provider-home"))

    async def run() -> None:
        reference = ChildReference("11111111-1111-4111-8111-111111111111", "native", "conversation")
        turn = TurnToken("conversation", 1)
        writes: list[TurnToken] = []
        fact = ProviderSubmissionFact(
            ConversationBackendKey.codex,
            "rejected" if fate == "rejected" else "accepted",
            "rpc_error" if fate == "rejected" else "turn/start",
            "correlated-request",
        )

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
                    raise BackendPromptRejected(
                        "provider refused request", provider_submission=fact
                    )
                return BackendPromptAccepted(True, provider_submission=fact)

        native = Native()
        spool = MacOutbox(tmp_path / "spool")
        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "native"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=cast(Any, lambda *args: lambda **kwargs: native),
            message_files=ConversationMessageFiles(tmp_path / "db"),
        )
        commands: list[ChildCommand] = []

        async def send(command: ChildCommand) -> Any:
            commands.append(command)
            return await registry.execute_command(command)

        remote = RemoteBackendChild(reference, send, lambda _: canonical_snapshot())
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
                with pytest.raises(RemoteCommandUncertain):
                    await write()
            elif fate == "rejected":
                with pytest.raises(BackendPromptRejected) as refused:
                    await write()
                assert refused.value.provider_submission == fact
            else:
                accepted = await write()
                assert accepted.provider_submission == fact
            command = commands[-1]
            recorded = registry.outcomes[command.command_id]
            expected = {
                "accepted": CommandDisposition.succeeded,
                "rejected": CommandDisposition.refused,
                "unknown": CommandDisposition.uncertain,
            }[fate]
            assert recorded.disposition is expected
            assert await registry.execute_command(command) == recorded
            assert writes == [turn]
            assert registry.children[reference.child_id].active_turn == (
                None if fate == "rejected" else turn
            )
        finally:
            spool.close()

    asyncio.run(run())
