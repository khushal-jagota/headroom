"""Native callback continuity is tested against the actual executor registry."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from tests.support.executor_control import MessageReader

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.admission.service import admission_scope
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    BackendSpawnFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import ToolCallStatus
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.contracts import (
    CatalogWorkspaceBinding,
    LaunchContext,
    ModelSelection,
    SelectionKind,
)
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION, ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    ConnectionState,
    DurableAnswer,
    EnvelopeKind,
    ExecutorIdentity,
    IpcOperation,
    IpcRequest,
    JsonObject,
    QueuedEnvelope,
    Quiescence,
    QuiesceRequest,
    ReceiptDisposition,
    ReconciliationResult,
    WireMessage,
    WireMessageKind,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.mac import MacExecutor, as_object
from planner.executor.runtime import NativeChildRegistry
from planner.tickets.contracts import TicketLocale


def _fixture_admission(
    registry: NativeChildRegistry,
    reference: ChildReference,
    resolved: ResolvedConversationStart,
    *,
    relative: str | None = None,
    localized: Path | None = None,
) -> AdmissionExpectation:
    """Supply exact synthetic ownership without catalog or configuration proof."""
    workspace = CatalogWorkspaceBinding(
        resolved.workspace_folder, resolved.workspace_path_policy, relative
    )
    context = LaunchContext(
        "fixture-context",
        TicketLocale.macos,
        resolved.backend_key,
        ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 1
        ),
        workspace,
        localized or resolved.workspace_folder,
        "fixture-executable",
        "1",
        "fixture-provider-scope",
        None,
    )
    attempt_id = str(uuid4())
    return AdmissionExpectation(
        ModelSelection(
            TicketLocale.macos,
            resolved.backend_key,
            resolved.model,
            SelectionKind.concrete,
            resolved.reasoning_effort,
            "fixture-selection",
        ),
        workspace,
        context,
        None,
        attempt_id,
        resolved.model,
        resolved.reasoning_effort,
        "fixture-policy",
        resolved.conversation_id,
        resolved_model_id=None,
        child=reference,
        executor_identity=context.executor_identity,
    )


def canonical_snapshot() -> CanonicalSkillSnapshot:
    names = ("panels", "panels-worker", "panels-worker-coding")
    return CanonicalSkillSnapshot(
        tuple(
            CanonicalSkillSource(
                name, f"---\nname: {name}\ndescription: test\n---\n{name} database text\n"
            )
            for name in names
        ),
        names,
    )


@pytest.fixture(autouse=True)
def isolated_native_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native-codex-home"))
    monkeypatch.setenv("PLAN_HERMES_HOME", str(tmp_path / "native-hermes-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "native-claude-home"))


class ScriptedChild:
    def __init__(self) -> None:
        self.answers: list[tuple[str, str]] = []
        self.starts = 0
        self.prompts = 0
        self.sink: BackendEventSink | None = None

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        self.starts += 1
        await asyncio.sleep(0)

    async def write_prompt(
        self, turn: TurnToken, content: MessageContent, **kwargs: object
    ) -> BackendPromptAccepted:
        self.prompts += 1
        return BackendPromptAccepted(True)

    async def steer(self, turn: TurnToken, content: MessageContent, **kwargs: object) -> None:
        raise RuntimeError("wire response lost after write")

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        self.answers.append((ask_id, option_id))
        await asyncio.sleep(0)

    async def stop(self) -> None:
        return None


@pytest.fixture
def native_registry(
    tmp_path: Path,
) -> Iterator[tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand]]:
    spool = FilesystemSpool(tmp_path / "spool")
    native = ScriptedChild()
    reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

    def factory(
        resolved: ResolvedConversationStart, capability: str, child: ChildReference
    ) -> BackendChildFactory:
        def create(**kwargs: object) -> BackendChild:
            native.sink = cast(BackendEventSink, kwargs["event_sink"])
            return cast(BackendChild, native)

        return cast(BackendChildFactory, create)

    registry = NativeChildRegistry(
        paths=ExecutorPaths(tmp_path / "executor"),
        incarnation=reference.executor_incarnation,
        spool=spool,
        factory=factory,
        message_files=ConversationMessageFiles(tmp_path / "unused"),
    )
    resolved = ResolvedConversationStart(
        "c_one",
        ConversationBackendKey.codex,
        "test",
        None,
        None,
        tmp_path,
        ConversationAccess.full,
    )
    start = ChildCommand(
        "start",
        reference,
        ChildOperation.start,
        None,
        {
            "canonical_skill_snapshot": encode_value(canonical_snapshot()),
            "resolved_start": encode_value(resolved),
        },
    )
    try:
        yield registry, spool, native, start
    finally:
        spool.close()


def acknowledge_pending(spool: FilesystemSpool) -> None:
    for row in spool.pending():
        spool.acknowledge(
            AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.accepted,
                {},
                1,
            )
        )


@pytest.mark.parametrize("boundary", ["incarnation", "start", "prompt", "prompt_lock"])
def test_pre_dispatch_refusal_never_calls_native(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    boundary: str,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command = start
        if boundary == "incarnation":
            command = replace(start, child=replace(start.child, executor_incarnation=str(uuid4())))
        if boundary.startswith("prompt"):
            assert (
                await registry.execute_command(start)
            ).disposition == CommandDisposition.succeeded
            command = ChildCommand(
                "prompt",
                start.child,
                ChildOperation.write_prompt,
                TurnToken("c_one", 1),
                {
                    "content": encode_value(text_message_content("go")),
                    "sender_content": encode_value(text_message_content("go")),
                    "sender_label": "Owner",
                    "mode": PromptDeliveryMode.queue.value,
                },
            )
        if boundary == "prompt_lock":
            child = registry.children[start.child.child_id]
            await child.lock.acquire()
            try:
                task = asyncio.create_task(registry.execute_command(command))
                # Let execute_command create its task, then let that task wait on the lock.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not task.done()
                assert not (await registry.quiesce()).ready
            finally:
                child.lock.release()
            outcome = await task
        else:
            if boundary == "prompt":
                registry.admission_stopped = True
            else:
                await registry.quiesce()
            outcome = await registry.execute_command(command)
        assert outcome.disposition == CommandDisposition.refused
        assert native.prompts == 0
        assert native.starts == (1 if boundary.startswith("prompt") else 0)
        if not boundary.startswith("prompt"):
            assert native.sink is None  # Native construction never occurred.
        else:
            # Closed admission did not install a new active turn.
            assert all(child.active_turn is None for child in registry.children.values())
        assert registry.outcomes[command.command_id] == outcome
        assert spool.pending()[-1].payload == encode_value(outcome)
        blocked = await registry.quiesce()
        assert blocked.uncertain_operation_count == 0
        assert blocked.unsent_entry_count > 0
        assert not blocked.ready
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready

    asyncio.run(run())


def test_refusal_publication_and_receipts_block_exit_and_duplicates_keep_first_outcome(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        await registry.quiesce()
        entered = asyncio.Event()
        released = asyncio.Event()
        publish = registry._publish_outcome

        async def paused_publish(outcome: CommandOutcome) -> None:
            entered.set()
            await released.wait()
            await publish(outcome)

        monkeypatch.setattr(registry, "_publish_outcome", paused_publish)
        task = asyncio.create_task(registry.execute_command(start))
        await entered.wait()
        assert registry.outcomes[start.command_id].disposition == CommandDisposition.refused
        assert spool.pending_count() == 0
        blocked = await registry.quiesce()
        assert blocked.uncertain_operation_count > 0
        assert not blocked.ready
        released.set()
        first = await task
        entries = spool.pending()
        assert await registry.execute_command(start) == first
        assert spool.pending() == entries
        with pytest.raises(ValueError, match="different content"):
            await registry.execute_command(replace(start, arguments={}))
        assert not (await registry.quiesce()).ready
        registry.confirm_command(start.command_id)
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        assert await registry.execute_command(start) == first
        assert spool.pending_count() == 0
        assert native.starts == 0 and native.sink is None

    asyncio.run(run())


def test_native_error_with_admission_text_stays_uncertain_after_confirmation_and_drain(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry

        async def failing_start(
            resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
        ) -> None:
            native.starts += 1
            raise ValueError("executor is quiescing")

        monkeypatch.setattr(native, "start", failing_start)
        outcome = await registry.execute_command(start)
        assert outcome.disposition == CommandDisposition.uncertain
        assert outcome.result == {
            "error_type": "ValueError",
            "error_message": "executor is quiescing",
        }
        assert native.starts == 1
        assert spool.pending()[-1].payload == encode_value(outcome)
        registry.confirm_command(start.command_id)
        acknowledge_pending(spool)
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, start.child.executor_incarnation, 1
        )
        assert registry.inventory(identity).retained_command_outcomes == ()
        blocked = await registry.quiesce()
        assert blocked.active_turn_count == 0
        assert blocked.pending_callback_count == 0
        assert blocked.unsent_entry_count == 0
        assert blocked.uncertain_operation_count == 1
        assert not blocked.ready
        assert await registry.execute_command(start) == outcome
        assert native.starts == 1

    asyncio.run(run())


async def uncertain_automatic_compaction(
    registry: NativeChildRegistry,
    native: ScriptedChild,
    start: ChildCommand,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ChildCommand, CommandOutcome]:
    assert (await registry.execute_command(start)).disposition == CommandDisposition.succeeded

    async def compacting_prompt(
        turn: TurnToken, content: MessageContent, **kwargs: object
    ) -> BackendPromptAccepted:
        assert kwargs["automatic_compaction"] is True
        native.prompts += 1
        raise RuntimeError("response lost after automatic compaction")

    monkeypatch.setattr(native, "write_prompt", compacting_prompt)
    command = ChildCommand(
        "compaction",
        start.child,
        ChildOperation.write_prompt,
        TurnToken(start.child.conversation_id, 1),
        {
            "content": encode_value(text_message_content("continue")),
            "sender_content": encode_value(text_message_content("continue")),
            "sender_label": "Owner",
            "mode": PromptDeliveryMode.queue.value,
            "automatic_compaction": True,
        },
    )
    outcome = await registry.execute_command(command)
    assert outcome.disposition == CommandDisposition.uncertain
    return command, outcome


def test_confirmed_exact_stop_allows_exit_without_rewriting_uncertain_compaction(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, first = await uncertain_automatic_compaction(registry, native, start, monkeypatch)
        entry = spool.pending()[-1]
        original_bytes = spool._db.execute(
            "SELECT envelope_json FROM envelopes WHERE submission_id=?", (entry.submission_id,)
        ).fetchone()[0]
        stop = ChildCommand("stop", start.child, ChildOperation.stop, None, {})
        assert (await registry.execute_command(stop)).disposition == CommandDisposition.succeeded
        assert start.child.child_id not in registry.children
        registry.confirm_command(command.command_id)
        blocked = await registry.quiesce()
        assert blocked.uncertain_operation_count == 0
        assert blocked.unsent_entry_count > 0
        assert not blocked.ready
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        assert registry.outcomes[command.command_id] == first
        assert await registry.execute_command(command) == first
        with pytest.raises(ValueError, match="different content"):
            await registry.execute_command(replace(command, arguments={}))
        assert (native.starts, native.prompts) == (1, 1)
        assert spool.pending_count() == 0
        assert spool._db.execute(
            "SELECT envelope_json,pending FROM envelopes WHERE submission_id=?",
            (entry.submission_id,),
        ).fetchone() == (original_bytes, 0)

    asyncio.run(run())


@pytest.mark.parametrize(
    "missing_evidence",
    [
        "no_stop",
        "absent_child",
        "failed_stop",
        "unacknowledged",
        "live_child",
        "other_incarnation",
        "other_child",
        "other_conversation",
        "outcome_mismatch",
        "not_stop",
    ],
)
def test_uncertainty_requires_acknowledged_exact_successful_stop_and_no_live_child(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    missing_evidence: str,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, first = await uncertain_automatic_compaction(registry, native, start, monkeypatch)
        child = registry.children[start.child.child_id]
        if missing_evidence == "absent_child":
            registry.children.pop(start.child.child_id)
        elif missing_evidence == "failed_stop":

            async def failed_stop() -> None:
                raise RuntimeError("termination not confirmed")

            monkeypatch.setattr(native, "stop", failed_stop)
            failed = await registry.execute_command(
                ChildCommand("stop", start.child, ChildOperation.stop, None, {})
            )
            assert failed.disposition == CommandDisposition.uncertain
            registry.confirm_command("stop")
        elif missing_evidence != "no_stop":
            stop = ChildCommand("stop", start.child, ChildOperation.stop, None, {})
            stopped = await registry.execute_command(stop)
            assert stopped.disposition == CommandDisposition.succeeded
            if missing_evidence == "live_child":
                registry.children[start.child.child_id] = child
            elif missing_evidence.startswith("other_"):
                changed = {
                    "other_incarnation": replace(start.child, executor_incarnation=str(uuid4())),
                    "other_child": replace(start.child, child_id=str(uuid4())),
                    "other_conversation": replace(start.child, conversation_id="c_other"),
                }[missing_evidence]
                # Retained successful termination of a different full reference is insufficient.
                registry._commands["stop"] = replace(stop, child=changed)
                registry.outcomes["stop"] = replace(stopped, child=changed)
            elif missing_evidence == "outcome_mismatch":
                registry.outcomes["stop"] = replace(
                    stopped, child=replace(start.child, conversation_id="c_other")
                )
            elif missing_evidence == "not_stop":
                registry._commands["stop"] = replace(stop, operation=ChildOperation.start)
        if missing_evidence != "unacknowledged":
            registry.confirm_command(command.command_id)
        acknowledge_pending(spool)
        blocked = await registry.quiesce()
        assert blocked.uncertain_operation_count == (2 if missing_evidence == "failed_stop" else 1)
        assert blocked.unsent_entry_count == 0
        assert not blocked.ready
        assert registry.outcomes[command.command_id] == first
        assert (native.starts, native.prompts) == (1, 1)

    asyncio.run(run())


def test_historical_uncertainty_does_not_clear_another_child_active_turn(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, _ = await uncertain_automatic_compaction(registry, native, start, monkeypatch)
        await registry.execute_command(
            ChildCommand("stop", start.child, ChildOperation.stop, None, {})
        )
        registry.confirm_command(command.command_id)
        other = replace(
            start, command_id="other_start", child=replace(start.child, child_id=str(uuid4()))
        )
        assert (await registry.execute_command(other)).disposition == CommandDisposition.succeeded
        registry.children[other.child.child_id].active_turn = TurnToken("c_one", 2)
        acknowledge_pending(spool)
        blocked = await registry.quiesce()
        assert blocked.uncertain_operation_count == 0
        assert blocked.active_turn_count == 1
        assert not blocked.ready
        assert other.child.child_id in registry.children

    asyncio.run(run())


def test_late_outcome_after_ready_requires_publication_acknowledgment_and_receipt(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, first = await uncertain_automatic_compaction(registry, native, start, monkeypatch)
        await registry.execute_command(
            ChildCommand("stop", start.child, ChildOperation.stop, None, {})
        )
        registry.confirm_command(command.command_id)
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        entered = asyncio.Event()
        release = asyncio.Event()
        publish = registry._publish_outcome

        async def paused_publish(outcome: CommandOutcome) -> None:
            entered.set()
            await release.wait()
            await publish(outcome)

        monkeypatch.setattr(registry, "_publish_outcome", paused_publish)
        late = replace(command, command_id="late_compaction")
        task = asyncio.create_task(registry.execute_command(late))
        await entered.wait()
        assert not task.done()
        assert spool.pending_count() == 0
        assert not (await registry.quiesce()).ready
        release.set()
        late_outcome = await task
        assert late_outcome.disposition == CommandDisposition.refused
        assert spool.pending_count() == 1
        # The retired identity refuses native work, but still needs its receipt.
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        registry.confirm_command(late.command_id)
        assert (await registry.quiesce()).ready
        assert await registry.execute_command(late) == late_outcome
        assert registry.outcomes[command.command_id] == first
        assert (native.starts, native.prompts) == (1, 1)

    asyncio.run(run())


def test_restart_preserves_uncertain_outcome_bytes_and_receipts_without_old_child_authority(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, first = await uncertain_automatic_compaction(registry, native, start, monkeypatch)
        capability = registry.children[start.child.child_id].capability
        await registry.execute_command(
            ChildCommand("stop", start.child, ChildOperation.stop, None, {})
        )
        registry.confirm_command(command.command_id)
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        before = spool._db.execute(
            "SELECT submission_id,envelope_json,pending FROM envelopes ORDER BY submission_id"
        ).fetchall()
        assert before and all(row[2] == 0 for row in before)
        assert any(
            TypeAdapter(QueuedEnvelope).validate_json(row[1]).payload == encode_value(first)
            for row in before
        )
        paths = ExecutorPaths(spool.root.parent)
        sender_id = spool.sender_id
        spool.close()
        executor = MacExecutor(
            paths=paths,
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        try:
            assert executor.spool.sender_id == sender_id
            assert (
                executor.spool._db.execute(
                    "SELECT submission_id,envelope_json,pending FROM envelopes "
                    "ORDER BY submission_id"
                ).fetchall()
                == before
            )
            assert executor.spool.pending_count() == 0
            assert executor.identity.executor_incarnation != start.child.executor_incarnation
            assert executor.registry.children == {}
            assert executor.registry.outcomes == {}
            with pytest.raises(PermissionError, match="unknown executor child capability"):
                executor.registry.authenticate(start.child.child_id, capability)
            refused = await executor.registry.execute_command(
                replace(command, command_id="stale_compaction")
            )
            assert refused.disposition == CommandDisposition.refused
            assert refused.result == {"reason": "incarnation_gone"}
            assert (native.starts, native.prompts) == (1, 1)
        finally:
            executor.spool.close()

    asyncio.run(run())


def test_pending_callback_and_native_command_survive_connection_task_cancel(tmp_path: Path) -> None:
    async def run() -> None:
        spool = FilesystemSpool(tmp_path / "spool")
        native = ScriptedChild()
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_example"),)),
            tmp_path,
            ConversationAccess.full,
        )
        start = ChildCommand(
            "start",
            reference,
            ChildOperation.start,
            None,
            {
                "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                "resolved_start": encode_value(resolved),
            },
        )
        expectation = _fixture_admission(registry, reference, resolved)
        start = replace(
            start,
            arguments={
                **start.arguments,
                "admission_expectation": encode_value(expectation),
            },
        )
        caller = asyncio.create_task(registry.execute_command(start))
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        outcome = await registry.execute_command(start)
        assert outcome.disposition == CommandDisposition.succeeded
        assert native.starts == 1
        with pytest.raises(ValueError, match="different content"):
            await registry.execute_command(replace(start, arguments={}))
        assert native.sink is not None
        turn = TurnToken("c_one", 1)
        prompt = ChildCommand(
            "prompt",
            reference,
            ChildOperation.write_prompt,
            turn,
            {
                "content": encode_value(text_message_content("go")),
                "sender_content": encode_value(text_message_content("go")),
                "sender_label": "Owner",
                "mode": PromptDeliveryMode.queue.value,
            },
        )
        prompt = replace(
            prompt,
            arguments={
                **prompt.arguments,
                "admission_expectation": encode_value(replace(expectation, turn=turn)),
            },
        )
        await registry.execute_command(prompt)
        uncertain = ChildCommand(
            "uncertain",
            reference,
            ChildOperation.steer,
            turn,
            {
                "content": encode_value(text_message_content("steer")),
                "sender_label": "Owner",
                "admission_expectation": encode_value(replace(expectation, turn=turn)),
            },
        )
        assert (
            await registry.execute_command(uncertain)
        ).disposition == CommandDisposition.uncertain
        assert (await registry.quiesce()).active_turn_count == 1
        artifact = tmp_path / "artifacts" / "tickets" / "t_example" / "result.bin"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"whole output")
        await native.sink.tool_call_finished(
            turn, tool_call_id="tool", tool_call_status=ToolCallStatus.completed, detail=None
        )
        output = spool.pending()[-1]
        assert output.file_dependencies[0].relative_path == "result.bin"
        assert spool.snapshots(output.submission_id)[0].local_path.read_bytes() == b"whole output"
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("ask", "Allow?", None, ())
        )
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 2
        )
        ask = registry.inventory(identity).children[0].pending_asks[0]
        assert native.answers == []  # absence of an answer remains a wait
        assert (await registry.quiesce()).pending_callback_count == 1
        missing = replace(ask, child=replace(reference, child_id=str(uuid4())))
        assert await registry.apply_answer(DurableAnswer("gone", missing, "allow", 1)) is None
        assert native.answers == []
        answer = DurableAnswer("answer", ask, "allow", 1)
        first = await registry.apply_answer(answer)
        assert first is not None
        assert await registry.apply_answer(answer) == first
        assert native.answers == [("ask", "allow")]
        assert registry.inventory(identity).children[0].pending_asks == ()
        kinds = [entry.kind for entry in spool.pending()]
        assert EnvelopeKind.answer_application in kinds
        assert (
            await registry.execute_command(uncertain)
        ).disposition == CommandDisposition.uncertain
        quiescence = await registry.quiesce()
        assert quiescence.uncertain_operation_count == 1
        assert quiescence.unsent_entry_count > 0
        assert not quiescence.ready
        spool.close()

    asyncio.run(run())


def test_stale_quiesce_rejects_restart_and_exit_is_identity_bound(tmp_path: Path) -> None:
    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        forbidden = await executor.handle_ipc(
            IpcRequest(
                "bad-child",
                IpcOperation.write,
                "unknown",
                "forged",
                {"operation": "ticket.note", "arguments": {}},
            )
        )
        assert forbidden.error is not None
        assert executor.spool.pending() == ()
        observed = executor.identity
        executor.identity = replace(observed, executor_incarnation=str(uuid4()))
        stale = QuiesceRequest(observed, os.getpid(), True)
        rejected = await executor.handle_ipc(
            IpcRequest("old", IpcOperation.quiesce, None, None, as_object(stale))
        )
        assert rejected.error is not None
        assert not executor.registry.admission_stopped
        fresh = QuiesceRequest(executor.identity, os.getpid(), True)
        accepted = await executor.handle_ipc(
            IpcRequest("fresh", IpcOperation.quiesce, None, None, as_object(fresh))
        )
        assert accepted.error is None
        assert accepted.result is not None
        assert accepted.result["exit_committed"] is True
        assert executor._stop.is_set()
        executor.spool.close()

    asyncio.run(run())


def test_incompatible_spool_does_not_mutate_any_path(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    root.mkdir()
    (root / "metadata.json").write_text(json.dumps({"format": 999, "sender_id": str(uuid4())}))
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")}
    with pytest.raises(ValueError, match="incompatible|metadata fields"):
        FilesystemSpool(root)
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")}
    assert after == before


def test_generation_change_during_quiesce_cannot_commit_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        original = executor.registry.quiesce

        async def changed_generation() -> Quiescence:
            result = await original()
            executor.identity = replace(executor.identity, connection_generation=1)
            return result

        monkeypatch.setattr(executor.registry, "quiesce", changed_generation)
        expected = QuiesceRequest(executor.identity, os.getpid(), True)
        response = await executor.handle_ipc(
            IpcRequest("race", IpcOperation.quiesce, None, None, as_object(expected))
        )
        assert response.error is not None
        assert not executor._stop.is_set()
        executor.spool.close()

    asyncio.run(run())


def test_quiesce_waits_for_start_and_rechecks_stop_emitted_journal(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        released = asyncio.Event()
        native = ScriptedChild()
        native_stops: list[bool] = []

        class StartingChild(ScriptedChild):
            async def start(
                self,
                resolved_start: ResolvedConversationStart,
                *,
                vendor_session_cursor: str | None,
            ) -> None:
                entered.set()
                await released.wait()

            async def stop(self) -> None:
                native_stops.append(True)
                assert self.sink is not None
                await self.sink.vendor_session_cursor_rebound("cursor-at-stop")

        native = StartingChild()
        spool = FilesystemSpool(tmp_path / "spool")
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        task = asyncio.create_task(
            registry.execute_command(
                ChildCommand(
                    "start",
                    reference,
                    ChildOperation.start,
                    None,
                    {
                        "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                        "resolved_start": encode_value(resolved),
                    },
                )
            )
        )
        await entered.wait()
        blocked = await registry.quiesce()
        assert not blocked.ready
        assert blocked.uncertain_operation_count > 0
        assert native_stops == []
        released.set()
        await task
        from planner.executor.contracts import AcceptanceReceipt, ReceiptDisposition

        pending_before_stop = spool.pending()
        assert pending_before_stop
        registry.begin_source_work(reference)
        assert (await registry.quiesce()).uncertain_operation_count == 1
        assert native_stops == []
        registry.end_source_work(reference)
        stopped_with_event = await registry.quiesce()
        assert native_stops == [True]
        assert not stopped_with_event.ready
        assert stopped_with_event.unsent_entry_count == len(pending_before_stop) + 1
        assert spool.pending()[:-1] == pending_before_stop
        for row in spool.pending():
            spool.acknowledge(
                AcceptanceReceipt(
                    row.sender_id,
                    row.submission_id,
                    row.payload_sha256,
                    ReceiptDisposition.accepted,
                    {},
                    1,
                )
            )
        assert (await registry.quiesce()).ready
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("wrapper", [False, True])
def test_applied_answer_recovers_when_outcome_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrapper: bool,
) -> None:
    async def run() -> None:
        from planner.executor.contracts import FileSnapshot

        spool = FilesystemSpool(tmp_path / "spool")
        native = ScriptedChild()
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        await registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                    "resolved_start": encode_value(resolved),
                },
            )
        )
        assert native.sink is not None
        turn = TurnToken("c_one", 1)
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("ask", "Allow?", None, ())
        )
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 1
        )
        ask = registry.inventory(identity).children[0].pending_asks[0]
        answer = DurableAnswer("answer", ask, "allow", 1)
        original = spool.mint_envelope
        failed = False

        def fail_outcome(
            *,
            conversation_id: str,
            kind: EnvelopeKind,
            payload: JsonObject,
            snapshots: tuple[FileSnapshot, ...] = (),
            submission_id: str | None = None,
        ) -> QueuedEnvelope:
            nonlocal failed
            if (
                kind == EnvelopeKind.command_outcome
                and payload.get("command_id") == "answer"
                and not failed
            ):
                failed = True
                raise OSError("disk failed after callback")
            return original(
                conversation_id=conversation_id,
                kind=kind,
                payload=payload,
                snapshots=snapshots,
                submission_id=submission_id,
            )

        monkeypatch.setattr(spool, "mint_envelope", fail_outcome)
        outer = ChildCommand(
            "outer",
            reference,
            ChildOperation.answer_permission_ask,
            turn,
            {"answer": encode_value(answer)},
        )
        with pytest.raises(OSError, match="after callback"):
            if wrapper:
                await registry.execute_command(outer)
            else:
                await registry.apply_answer(answer)
        monkeypatch.setattr(spool, "mint_envelope", original)
        assert native.answers == [("ask", "allow")]
        assert registry.inventory(identity).children[0].pending_asks == ()
        assert not (await registry.quiesce()).ready
        if wrapper:
            outcome = await registry.execute_command(outer)
            assert outcome.disposition == CommandDisposition.succeeded
            assert outcome.result == {"applied": True}
            assert await registry.execute_command(outer) == outcome
        recovered = await registry.apply_answer(answer)
        assert recovered is not None
        assert native.answers == [("ask", "allow")]
        assert registry.outcomes["answer"].result["answer_application"] == encode_value(recovered)
        assert any(row.kind == EnvelopeKind.answer_application for row in spool.pending())
        spool.close()

    asyncio.run(run())


def test_replay_protocol_error_surfaces_and_preserves_pending_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        executor._handshake_accepted = True
        for conversation in ("c_a", "c_a"):
            executor.spool.mint_envelope(
                conversation_id=conversation, kind=EnvelopeKind.cli_write, payload={}
            )
        visited: list[str] = []

        async def transport(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            row = TypeAdapter(QueuedEnvelope).validate_python(payload)
            visited.append(row.conversation_id)
            raise ValueError("invalid protocol response")

        monkeypatch.setattr(executor, "_request", transport)
        pending = executor.spool.pending()
        with pytest.raises(ValueError, match="invalid protocol response"):
            await executor._replay()
        assert visited == ["c_a"]
        assert executor.spool.pending() == pending
        executor.spool.close()

    asyncio.run(run())


def test_ipc_snapshot_owner_is_bound_and_refusal_is_an_error(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        from planner.executor.contracts import AcceptanceReceipt, ReceiptDisposition

        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        native = ScriptedChild()

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            return cast(BackendChildFactory, lambda **kwargs: native)

        executor.registry.factory = factory
        reference = ChildReference(executor.identity.executor_incarnation, str(uuid4()), "c_one")
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_own"),)),
            tmp_path,
            ConversationAccess.full,
        )
        expectation = _fixture_admission(executor.registry, reference, resolved)
        await executor.registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                    "resolved_start": encode_value(resolved),
                    "admission_expectation": encode_value(expectation),
                },
            )
        )
        for owner in ("t_own", "t_other"):
            root = executor.paths.artifacts / owner
            root.mkdir(parents=True, exist_ok=True)
            (root / "file").write_text(owner)
        child = executor.registry.children[reference.child_id]
        request = IpcRequest(
            "write",
            IpcOperation.write,
            reference.child_id,
            child.capability,
            {"operation": "ticket.note", "arguments": {"ticket_id": "t_other"}},
        )
        task = asyncio.create_task(executor.handle_ipc(request))
        while not any(row.submission_id == "write" for row in executor.spool.pending()):
            await asyncio.sleep(0)
        row = next(row for row in executor.spool.pending() if row.submission_id == "write")
        assert {manifest.owner_id for manifest in row.file_dependencies} == {"t_own"}
        failure: JsonObject = {"code": "agent_forbidden", "message": "stale claim", "detail": {}}
        executor._receipts["write"].set_result(
            AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.refused,
                {"error": failure},
                1,
            )
        )
        response = await task
        assert response.result is None
        assert response.queued is None
        assert response.error == failure
        executor.registry.admission_stopped = True
        count = executor.spool.pending_count()
        refused = await executor.handle_ipc(replace(request, request_id="after-closure"))
        assert refused.error == {
            "code": "agent_forbidden",
            "message": "executor admission stopped",
            "detail": {},
        }
        assert executor.spool.pending_count() == count
        executor.spool.close()

    asyncio.run(run())


def test_real_daemon_controlled_quiesce_exits_zero(tmp_path: Path) -> None:
    import socket
    import subprocess
    import sys

    from planner.executor.contracts import IpcResponse
    from planner.executor.wire import encode_frame

    root = tmp_path / "daemon"
    script = (
        "import asyncio; from pathlib import Path; "
        "from planner.executor.mac import MacExecutor; "
        "from planner.executor.configuration import ExecutorPaths, PROTOCOL_VERSION; "
        f"service=MacExecutor(paths=ExecutorPaths(Path({str(root)!r})),revision={'a' * 40!r},"
        "executor_id='owner-mac',panels_server_url='https://127.0.0.1:1'); "
        "asyncio.run(service.run()); "
        "assert service.exit_committed"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    def exchange(request: IpcRequest) -> IpcResponse:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(5)
            stream.connect(str(root / "run" / "executor.sock"))
            stream.sendall(encode_frame(request))

            def exact(size: int) -> bytes:
                result = b""
                while len(result) < size:
                    data = stream.recv(size - len(result))
                    if not data:
                        raise AssertionError("daemon closed without response")
                    result += data
                return result

            length = int.from_bytes(exact(4), "big")
            return TypeAdapter(IpcResponse).validate_json(exact(length))

    try:
        deadline = time.monotonic() + 10
        while not (root / "run" / "executor.sock").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        health = exchange(IpcRequest("health", IpcOperation.health, None, None, {}))
        assert health.result is not None
        identity = TypeAdapter(ExecutorIdentity).validate_python(health.result["identity"])
        request = QuiesceRequest(identity, process.pid, True)
        result = exchange(IpcRequest("exit", IpcOperation.quiesce, None, None, as_object(request)))
        assert result.result is not None
        assert result.result["exit_committed"] is True
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("backend", list(ConversationBackendKey))
@pytest.mark.parametrize("policy", list(WorkspacePathPolicy))
def test_native_start_localizes_vps_identity_and_home_relative_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: WorkspacePathPolicy,
    backend: ConversationBackendKey,
) -> None:
    async def run() -> None:
        vps_home, mac_home = tmp_path / "vps-home", tmp_path / "mac-home"
        workspace = mac_home / "projects" / "same-checkout"
        workspace.mkdir(parents=True)
        configured_workspace = vps_home / "projects" / "same-checkout"
        if policy is WorkspacePathPolicy.native_absolute:
            # An available mapped checkout must not mask accidental home translation.
            configured_workspace.mkdir(parents=True)
            workspace = configured_workspace
        monkeypatch.setenv("HOME", str(mac_home))
        monkeypatch.setenv("CODEX_HOME", str(mac_home / ".codex"))
        monkeypatch.setenv("PLAN_HERMES_HOME", str(mac_home / ".hermes"))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(mac_home / " claude config "))
        native_home = {
            ConversationBackendKey.codex: mac_home / ".codex",
            ConversationBackendKey.hermes: mac_home / ".hermes",
            ConversationBackendKey.claude: mac_home / " claude config ",
        }[backend]
        paths = ExecutorPaths(mac_home / "executor-data")
        spool = FilesystemSpool(paths.spool)
        received: list[ResolvedConversationStart] = []
        constructed: list[ResolvedConversationStart] = []

        class InspectingChild(ScriptedChild):
            async def start(
                self,
                resolved_start: ResolvedConversationStart,
                *,
                vendor_session_cursor: str | None,
            ) -> None:
                received.append(resolved_start)
                assert vendor_session_cursor == "retained-native-session"
                for source in canonical_snapshot().sources:
                    assert (
                        native_home / "skills" / source.skill_name / "SKILL.md"
                    ).read_bytes() == source.source_text.encode("utf-8")
                assert resolved_start.role_materials is not None
                assert resolved_start.role_materials.role_text == "worker"
                environment = dict(resolved_start.role_materials.identity_environment_variables)
                generation_skills = Path(environment["PLAN_NATIVE_SKILLS_DIR"])
                assert generation_skills.parent.parent == paths.skills
                for source in canonical_snapshot().sources:
                    assert (generation_skills / source.skill_name / "SKILL.md").read_bytes() == (
                        source.source_text.encode("utf-8")
                    )
                (Path(environment["PLAN_TICKET_FILES_DIR"]) / "proof.txt").write_text(
                    "native artifact"
                )

        native = InspectingChild()

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            # Bootstrap is complete before provider construction.
            for source in canonical_snapshot().sources:
                assert (native_home / "skills" / source.skill_name / "SKILL.md").read_bytes() == (
                    source.source_text.encode("utf-8")
                )
            constructed.append(resolved)
            return cast(BackendChildFactory, lambda **kwargs: native)

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")
        registry = NativeChildRegistry(
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            paths=paths,
            workspace_home=mac_home,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        remote_identity = (
            ("PLAN_TICKET_ID", "t_own"),
            ("PLAN_TICKET_FILES_DIR", str(vps_home / "files" / "tickets" / "t_own")),
            ("PLAN_EXECUTOR_ROOT", str(vps_home / "wrong-root")),
            ("PLAN_EXECUTOR_CHILD_ID", "forged-child"),
            ("PLAN_EXECUTOR_CAPABILITY", "forged-capability"),
            ("HOME", str(vps_home)),
            ("CODEX_HOME", str(vps_home / ".codex")),
            ("HERMES_HOME", str(vps_home / ".hermes")),
            ("CLAUDE_CONFIG_DIR", str(vps_home / ".claude")),
            ("PLAN_NATIVE_SKILLS_DIR", str(vps_home / "forged-skills")),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            backend,
            "test",
            None,
            ConversationRoleMaterials("worker", remote_identity),
            configured_workspace,
            ConversationAccess.full,
            workspace_path_policy=policy,
        )
        from planner.executor.remote import RemoteBackendChild

        forwarded: list[ChildCommand] = []

        async def send(command: ChildCommand) -> CommandOutcome:
            forwarded.append(command)
            return await registry.execute_command(command)

        # Exercise VPS encoding and Mac hydration with distinct home directories.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: vps_home))
        remote = RemoteBackendChild(reference, send, lambda start: canonical_snapshot())
        if backend is ConversationBackendKey.hermes:
            await remote.start(resolved, vendor_session_cursor="retained-native-session")
        else:
            expectation = _fixture_admission(
                registry,
                reference,
                resolved,
                relative=(
                    "projects/same-checkout"
                    if policy is WorkspacePathPolicy.legacy_home_mapping
                    else None
                ),
                localized=workspace,
            )
            with admission_scope(expectation):
                await remote.start(resolved, vendor_session_cursor="retained-native-session")
        if policy is WorkspacePathPolicy.legacy_home_mapping:
            assert forwarded[0].arguments["workspace_home_relative"] == "projects/same-checkout"
        else:
            assert "workspace_home_relative" not in forwarded[0].arguments
            forwarded_start = TypeAdapter(ResolvedConversationStart).validate_python(
                forwarded[0].arguments["resolved_start"]
            )
            assert forwarded_start.workspace_folder == configured_workspace
        assert (
            registry.outcomes[forwarded[0].command_id].disposition == CommandDisposition.succeeded
        )
        assert received == constructed
        assert (paths.artifacts / "t_own" / "proof.txt").read_text() == "native artifact"
        assert received[0].workspace_folder == workspace
        assert received[0].role_materials is not None
        identity = dict(received[0].role_materials.identity_environment_variables)
        assert identity["PLAN_TICKET_FILES_DIR"] == str(paths.artifacts / "t_own")
        assert identity["PLAN_EXECUTOR_ROOT"] == str(paths.root)
        assert identity["PLAN_EXECUTOR_CHILD_ID"] == reference.child_id
        assert (
            identity["PLAN_EXECUTOR_CAPABILITY"] == registry.children[reference.child_id].capability
        )
        assert identity["PLAN_EXECUTOR_CAPABILITY"] != "forged-capability"
        assert not {"HOME", "CODEX_HOME", "HERMES_HOME", "CLAUDE_CONFIG_DIR"} & identity.keys()
        assert identity["PLAN_NATIVE_SKILLS_DIR"] != str(vps_home / "forged-skills")
        assert os.environ["HOME"] == str(mac_home)
        assert os.environ["CODEX_HOME"] == str(mac_home / ".codex")
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("relative", ["projects/missing", "../escape", "/absolute", "a\\b"])
def test_unavailable_or_unsafe_workspace_refuses_before_native_creation(
    tmp_path: Path,
    relative: str,
) -> None:
    async def run() -> None:
        spool = FilesystemSpool(tmp_path / "spool")
        created: list[bool] = []

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            created.append(True)
            return cast(BackendChildFactory, lambda **kwargs: ScriptedChild())

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")
        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            workspace_home=tmp_path,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            Path("/home/vps/projects/absent"),
            ConversationAccess.full,
        )
        result = await registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                    "resolved_start": encode_value(resolved),
                    "workspace_home_relative": relative,
                },
            )
        )
        assert result.disposition == CommandDisposition.refused
        assert result.result["error_type"] == "BackendSpawnFailed"
        assert created == []
        assert registry.children == {}
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "command_outcome", "answer_application"])
def test_user_input_recorded_map_survives_lost_ack_and_durability_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    from planner.conversation.backends.contracts import BackendUserInputRequest
    from planner.conversation.events import UserInputAnswer, UserInputOption, UserInputQuestion
    from planner.executor.contracts import AnswerApplication, AskKind, AskReference, FileSnapshot

    async def run() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        maps: list[tuple[str, tuple[UserInputAnswer, ...]]] = []

        class QuestionChild(ScriptedChild):
            async def answer_user_input(
                self, request_id: str, answers: tuple[UserInputAnswer, ...]
            ) -> None:
                maps.append((request_id, answers))
                entered.set()
                await release.wait()

        native = QuestionChild()
        spool = FilesystemSpool(tmp_path / "spool")
        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            def create(**kwargs: object) -> BackendChild:
                native.sink = cast(BackendEventSink, kwargs["event_sink"])
                return cast(BackendChild, native)

            return cast(BackendChildFactory, create)

        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        await registry.execute_command(
            ChildCommand(
                "start",
                reference,
                ChildOperation.start,
                None,
                {
                    "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                    "resolved_start": encode_value(resolved),
                },
            )
        )
        turn = TurnToken("c_one", 1)
        await registry.execute_command(
            ChildCommand(
                "prompt",
                reference,
                ChildOperation.write_prompt,
                turn,
                {
                    "content": encode_value(text_message_content("go")),
                    "sender_content": encode_value(text_message_content("go")),
                    "sender_label": "Owner",
                    "mode": PromptDeliveryMode.queue.value,
                },
            )
        )
        questions = (
            UserInputQuestion(
                "choice",
                "Choice",
                "Select one",
                (UserInputOption("Yes", ""), UserInputOption("No", "")),
                False,
                False,
            ),
            UserInputQuestion("text", "Text", "Explain", (), False, True),
        )
        assert native.sink is not None
        await native.sink.user_input_requested(turn, BackendUserInputRequest("same", questions))
        # Provider IDs can coincide across the two ask kinds.
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("same", "Allow?", None, ())
        )
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 2
        )
        inventory = registry.inventory(identity).children[0]
        assert inventory.active_turn == turn
        assert inventory.pending_user_input_ids == ("same",)
        ask = AskReference(reference, turn, "same", AskKind.user_input)
        answers = (UserInputAnswer("choice", ("Yes",)), UserInputAnswer("text", ("exact text",)))
        answer = DurableAnswer("map-answer", ask, None, 1, answers)
        assert maps == []  # Unanswered requests remain waits, including reconnect inventory.
        assert (await registry.quiesce()).pending_callback_count == 2
        missing = replace(ask, child=replace(reference, child_id=str(uuid4())))
        assert await registry.apply_answer(replace(answer, ask=missing)) is None
        for invalid in (answers[:1], tuple(reversed(answers))):
            with pytest.raises(ValueError, match="canonical"):
                await registry.apply_answer(replace(answer, user_input_answers=invalid))
        assert maps == []
        original = spool.mint_envelope
        failed = False

        def failing(
            *,
            conversation_id: str,
            kind: EnvelopeKind,
            payload: JsonObject,
            snapshots: tuple[FileSnapshot, ...] = (),
            submission_id: str | None = None,
        ) -> QueuedEnvelope:
            nonlocal failed
            if (
                failure is not None
                and kind.value == failure
                and not failed
                and (
                    kind == EnvelopeKind.answer_application
                    or payload.get("command_id") == answer.answer_id
                )
            ):
                failed = True
                raise OSError("disk failed after native map application")
            return original(
                conversation_id=conversation_id,
                kind=kind,
                payload=payload,
                snapshots=snapshots,
                submission_id=submission_id,
            )

        monkeypatch.setattr(spool, "mint_envelope", failing)
        outer = ChildCommand(
            "outer-map",
            reference,
            ChildOperation.answer_user_input,
            turn,
            {"answer": encode_value(answer)},
        )
        caller = asyncio.create_task(registry.execute_command(outer))
        await entered.wait()
        caller.cancel()  # The socket/caller disappears; native operation remains owned.
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        if failure is not None:
            with pytest.raises(OSError, match="after native"):
                await registry.execute_command(outer)
            monkeypatch.setattr(spool, "mint_envelope", original)
        outcome = await registry.execute_command(outer)
        assert outcome.disposition == CommandDisposition.succeeded
        assert await registry.execute_command(outer) == outcome
        assert maps == [("same", answers)]
        inventory = registry.inventory(identity).children[0]
        assert inventory.pending_user_input_ids == ()
        assert inventory.pending_asks[0].kind == AskKind.permission
        application = await registry.apply_answer(answer)
        assert application is not None
        assert application.ask.kind == AskKind.user_input
        evidence = registry.outcomes[answer.answer_id].result["answer_application"]
        assert TypeAdapter(AnswerApplication).validate_python(evidence) == application
        with pytest.raises(ValueError, match="different recorded"):
            await registry.apply_answer(
                replace(answer, user_input_answers=(UserInputAnswer("choice", ("No",)), answers[1]))
            )
        assert maps == [("same", answers)]
        spool.close()

    asyncio.run(run())


def test_old_peer_is_refused_before_work_or_replay_and_preserves_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.contracts import WireMessage, WireMessageKind
    from planner.executor.wire import ProtocolRefused

    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        original = peer.reply

        async def old_reply(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            if kind is WireMessageKind.hello:
                return WireMessage(
                    WireMessageKind.hello_accepted,
                    "hello",
                    as_object(replace(executor.identity, protocol_version=8)),
                )
            return await original(kind, payload)

        monkeypatch.setattr(executor, "_request", old_reply)
        row = executor.spool.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"original": True}
        )
        with pytest.raises(ProtocolRefused):
            await executor._connected()
        assert executor.spool.pending() == (row,)
        assert not executor._command_tasks and peer.closed
        executor.spool.close()

    asyncio.run(run())


def make_executor(root: Path) -> MacExecutor:
    return MacExecutor(
        paths=ExecutorPaths(root),
        revision="a" * 40,
        executor_id="owner-mac",
        panels_server_url="https://unused",
    )


class ControlPeer:
    """Control-only peer. Connected tests cover actual HTTP and WebSocket I/O."""

    def __init__(self, executor: MacExecutor) -> None:
        self.executor = executor
        self.reader = asyncio.StreamReader()
        self.closed = False
        self.failure: Exception | None = None

    async def receive_bytes(self, max_bytes: int) -> bytes:
        return await self.reader.read(max_bytes)

    async def send_bytes(self, data: bytes) -> None:
        pass

    async def aclose(self) -> None:
        self.closed = True
        self.reader.feed_eof()

    async def reply(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        from planner.executor.contracts import WireMessage, WireMessageKind

        result: JsonObject
        if kind is WireMessageKind.hello:
            result = as_object(self.executor.identity)
            response = WireMessageKind.hello_accepted
        elif kind is WireMessageKind.transport_open:
            result = {}
            response = WireMessageKind.transport_opened
        elif kind is WireMessageKind.reconcile_inventory:
            result = as_object(ReconciliationResult(self.executor.identity, (), (), (), ()))
            response = WireMessageKind.reconcile_result
        else:
            assert kind is WireMessageKind.reconciliation_ready
            result = {"ready": True}
            response = kind
        return WireMessage(response, "reply", result)


def install_control_peer(executor: MacExecutor, monkeypatch: pytest.MonkeyPatch) -> ControlPeer:
    from contextlib import asynccontextmanager

    from planner.executor import mac as module

    peer = ControlPeer(executor)

    from types import SimpleNamespace
    from unittest.mock import Mock

    @asynccontextmanager
    async def websocket(*args: object) -> AsyncIterator[ControlPeer]:
        if peer.failure is not None:
            raise peer.failure
        peer.closed = False
        peer.reader = asyncio.StreamReader()
        try:
            yield peer
        finally:
            await peer.aclose()

    client = SimpleNamespace(websocket=websocket, close=peer.aclose)
    monkeypatch.setattr(module, "ExecutorAppClient", Mock(return_value=client))
    monkeypatch.setattr(executor, "_request", peer.reply)
    return peer


def test_websocket_failure_closes_client_and_records_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)

        peer.failure = ConnectionError("WebSocket refused")
        with pytest.raises(ConnectionError, match="WebSocket refused"):
            await executor._connected()
        assert executor._app_client is None and executor.lost_at_ms is not None
        assert executor.state is ConnectionState.disconnected
        executor.spool.close()

    asyncio.run(run())


def test_controlled_stop_wakes_pending_handshake_without_peer_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        monkeypatch.setattr(executor, "_request", MacExecutor._request.__get__(executor))
        task = asyncio.create_task(executor._connected())
        try:
            while executor._writer is None:
                await asyncio.sleep(0)
            executor._stop.set()
            await asyncio.wait_for(task, 1)
            assert executor._writer is None and peer.closed
            assert not executor.spool.pending()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("case", ["valid", "no_handshake", "bad_payload", "stale_request"])
def test_ready_ack_clears_loss_before_fulfilling_current_request(tmp_path: Path, case: str) -> None:
    from planner.executor.contracts import ConnectionState, WireMessage, WireMessageKind
    from planner.executor.wire import ProtocolRefused, encode_message

    async def run() -> None:
        executor = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        executor.state = ConnectionState.reconciling
        executor.lost_at_ms = 123
        executor._handshake_accepted = case != "no_handshake"
        observed: list[tuple[ConnectionState, int | None]] = []

        class ObservedFuture(asyncio.Future[WireMessage]):
            def set_result(self, result: WireMessage) -> None:
                # Observe synchronously before fulfillment can resume the session.
                observed.append((executor.state, executor.lost_at_ms))
                super().set_result(result)

        pending = ObservedFuture()
        executor._pending["current-request"] = pending
        reader = MessageReader()
        reader.feed_data(
            encode_message(
                WireMessage(
                    WireMessageKind.reconciliation_ready,
                    "old-request" if case == "stale_request" else "current-request",
                    {"ready": False} if case == "bad_payload" else {"ready": True},
                )
            )
        )
        reader.feed_eof()
        try:
            if case in ("no_handshake", "bad_payload"):
                with pytest.raises(ProtocolRefused, match="invalid reconciliation"):
                    await executor._read_loop(reader)
            else:
                with pytest.raises(ConnectionError):
                    await executor._read_loop(reader)
            if case == "valid":
                assert observed == [(ConnectionState.ready, None)]
                assert pending.done()
            else:
                assert observed == []
                assert not pending.done()
                assert executor.state == ConnectionState.reconciling
                assert executor.lost_at_ms == 123
        finally:
            pending.cancel()
            executor.spool.close()

    asyncio.run(run())


def test_current_loss_survives_failed_reconnect_and_clears_only_when_reconciled_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import mac as module

    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        monkeypatch.setattr(module, "RECONNECT_INTERVAL_SECONDS", 0.001)

        async def ready() -> None:
            while executor.state is not ConnectionState.ready:
                await asyncio.sleep(0)

        task = asyncio.create_task(executor._connected())
        await asyncio.wait_for(ready(), 2)
        assert executor.lost_at_ms is None
        peer.reader.feed_eof()
        with pytest.raises(ConnectionError):
            await task
        loss = executor.lost_at_ms
        assert loss is not None
        peer.failure = OSError("peer unavailable")
        with pytest.raises(OSError):
            await executor._connected()
        assert executor.lost_at_ms == loss
        assert executor.state is ConnectionState.disconnected
        peer.failure = None
        task = asyncio.create_task(executor._connected())
        try:
            await asyncio.wait_for(ready(), 2)
            assert executor.lost_at_ms is None
            assert executor.identity.connection_generation == 3
            executor._stop.set()
            await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_replay_keeps_one_head_per_conversation_and_progresses_past_bulk_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled file upload cannot hold another conversation's control envelope."""
    from planner.executor.contracts import (
        AcceptanceReceipt,
        FileScope,
        FileTransfer,
        ReceiptDisposition,
    )
    from planner.executor.files import snapshot_file

    async def run() -> None:
        executor = make_executor(tmp_path)
        executor._handshake_accepted = True
        source = tmp_path / "artifacts"
        source.mkdir()
        (source / "image").write_bytes(b"immutable")
        snapshot = snapshot_file(
            root=source, relative_path="image", scope=FileScope.ticket, owner_id="t_one"
        )
        child = ChildReference(executor.identity.executor_incarnation, str(uuid4()), "c_a")
        first = executor.spool.mint_envelope(
            conversation_id="c_a",
            kind=EnvelopeKind.cli_write,
            payload={"child": as_object(child)},
            snapshots=(snapshot,),
        )
        second = executor.spool.mint_envelope(
            conversation_id="c_a", kind=EnvelopeKind.cli_write, payload={}
        )
        independent = executor.spool.mint_envelope(
            conversation_id="c_b", kind=EnvelopeKind.cli_write, payload={}
        )
        durable = executor.spool.snapshots(first.submission_id)[0].local_path
        uploading, release, progressed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent: list[str] = []

        async def upload(transfer: FileTransfer, source: Path) -> None:
            uploading.set()
            await release.wait()
            assert source.read_bytes() == b"immutable"

        def receipt(row: QueuedEnvelope) -> AcceptanceReceipt:
            return AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.accepted,
                {},
                1,
            )

        async def transport(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            if kind is WireMessageKind.file_manifest:
                return WireMessage(
                    WireMessageKind.file_slot,
                    "slot",
                    {
                        "transfer_id": "authorized",
                        "direction": "upload",
                        "manifest": payload["manifest"],
                    },
                )
            if kind is WireMessageKind.file_complete:
                return WireMessage(
                    WireMessageKind.file_accepted, "complete", cast(JsonObject, payload["manifest"])
                )
            row = TypeAdapter(QueuedEnvelope).validate_python(payload)
            sent.append(row.submission_id)
            if row == independent:
                progressed.set()
            return WireMessage(WireMessageKind.receipt, "receipt", as_object(receipt(row)))

        from types import SimpleNamespace

        from planner.executor.app_transport import ExecutorAppClient

        executor._app_client = cast(ExecutorAppClient, SimpleNamespace(upload=upload))
        monkeypatch.setattr(executor, "_request", transport)
        task = asyncio.create_task(executor._replay())
        try:
            await asyncio.wait_for(uploading.wait(), 2)
            await asyncio.wait_for(progressed.wait(), 2)
            assert not task.done() and second.submission_id not in sent
            # Reconciliation can persist a receipt during a live transfer. The pin
            # protects its source bytes until the transfer coroutine leaves.
            executor.spool.acknowledge(receipt(first))
            assert durable.exists()
            release.set()
            await asyncio.wait_for(task, 2)
            assert not durable.exists()
            assert sent.index(first.submission_id) < sent.index(second.submission_id)
            assert executor.spool.pending() == ()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_quiesce_counts_admitted_input_task_before_it_starts_and_refuses_new_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drain receipt cannot overtake an admitted but unscheduled transfer."""
    from unittest.mock import AsyncMock

    from planner.executor.contracts import (
        FileManifest,
        FileScope,
        FileTransfer,
        InputTransferRequest,
    )
    from planner.executor.wire import ProtocolRefused, encode_message

    async def run() -> None:
        executor = make_executor(tmp_path)
        pending = asyncio.create_task(asyncio.Event().wait())
        executor._transfer_tasks.add(cast(asyncio.Task[None], pending))
        send = AsyncMock()
        monkeypatch.setattr(executor, "_send", send)
        try:
            request = QuiesceRequest(executor.identity, os.getpid(), True)
            response = await executor.handle_ipc(
                IpcRequest("drain", IpcOperation.quiesce, None, None, as_object(request))
            )
            assert response.error is None and response.result is not None
            assert response.result["exit_committed"] is False
            assert not executor._stop.is_set()
            slot = FileTransfer(
                "transfer",
                "download",
                FileManifest("publication", FileScope.conversation, "c_one", "file", "a" * 64, 1),
            )
            reader = MessageReader()
            reader.feed_data(
                encode_message(
                    WireMessage(
                        WireMessageKind.file_slot,
                        "late-input",
                        as_object(InputTransferRequest("command", slot)),
                    )
                )
            )
            with pytest.raises(ProtocolRefused, match="handshake"):
                await executor._read_loop(reader)
            send.assert_not_called()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_cleanup_failure_still_revokes_generation_drains_tasks_and_closes_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        exited = asyncio.Event()
        task = asyncio.create_task(executor._connected())
        try:
            while executor.state is not ConnectionState.ready:
                await asyncio.sleep(0)
            client = executor._app_client
            assert client is not None

            async def broken_close() -> None:
                assert executor._writer is None and not executor._handshake_accepted
                exited.set()
                await peer.aclose()
                raise TimeoutError("transport cleanup failed")

            monkeypatch.setattr(client, "close", broken_close)
            executor._stop.set()
            with pytest.raises(TimeoutError, match="cleanup failed"):
                await asyncio.wait_for(task, 2)
            assert peer.closed and exited.is_set()
            assert executor._writer is None and executor._app_client is None
            assert executor.health().connection_state is ConnectionState.disconnected
            assert not executor._pending and not executor._transfer_tasks
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_disconnect_closes_network_before_waiting_for_canceled_http_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An HTTP transfer can wait for connection closure during canceled cleanup."""

    async def run() -> None:
        executor = make_executor(tmp_path)
        peer = install_control_peer(executor, monkeypatch)
        closed, canceled, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def close(connection: object) -> None:
            peer.closed = True
            peer.reader.feed_eof()
            closed.set()

        async def transfer() -> None:
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                canceled.set()
                # Models an HTTP response context that waits for transport closure.
                await closed.wait()

        connection = asyncio.create_task(executor._connected())
        try:
            while executor.state is not ConnectionState.ready:
                await asyncio.sleep(0)
            assert executor._app_client is not None
            monkeypatch.setattr(executor._app_client, "close", lambda: close(None))
            blocked = asyncio.create_task(transfer())
            executor._transfer_tasks.add(blocked)
            blocked.add_done_callback(executor._transfer_tasks.discard)
            await entered.wait()
            executor._stop.set()
            await asyncio.wait_for(connection, 2)
            assert closed.is_set() and canceled.is_set()
            assert not executor._transfer_tasks
        finally:
            closed.set()
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_input_capacity_refusal_replies_instead_of_abandoning_waiting_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from planner.executor.contracts import (
        FileManifest,
        FileScope,
        FileTransfer,
        InputTransferRequest,
    )
    from planner.executor.input_transfers import InputTransferDeadline
    from planner.executor.wire import encode_message

    async def run() -> None:
        executor = make_executor(tmp_path)
        executor._handshake_accepted = True
        from types import SimpleNamespace

        from planner.executor.app_transport import ExecutorAppClient

        executor._app_client = cast(ExecutorAppClient, SimpleNamespace())
        executor._transfers = 4
        send = AsyncMock()
        monkeypatch.setattr(executor, "_send", send)
        slot = FileTransfer(
            "transfer",
            "download",
            FileManifest("publication", FileScope.conversation, "c_one", "input", "a" * 64, 1),
        )
        executor._input_transfers = {
            str(index): InputTransferDeadline(InputTransferRequest(str(index), slot))
            for index in range(4)
        }
        reader = MessageReader()
        reader.feed_data(
            encode_message(
                WireMessage(
                    WireMessageKind.file_slot,
                    "caller",
                    as_object(InputTransferRequest("command", slot)),
                )
            )
        )
        reader.feed_eof()
        try:
            with pytest.raises(ConnectionError):
                await executor._read_loop(reader)
            message = send.call_args.args[0]
            assert message.kind is WireMessageKind.input_failed and message.request_id == "caller"
            assert "capacity" in message.payload["message"]
            assert executor._transfers == 4
        finally:
            executor.spool.close()

    asyncio.run(run())


def test_cancelled_source_publication_remains_counted_until_durable_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation cannot let quiescence overtake a still-running journal thread."""
    import threading

    async def run() -> None:
        executor = make_executor(tmp_path)
        reference = ChildReference(executor.identity.executor_incarnation, str(uuid4()), "c_one")
        entered = threading.Event()
        release = threading.Event()
        from types import SimpleNamespace

        # Authentication is covered by the existing IPC owner test. Exercise the
        # real IPC publication boundary and real filesystem journal here.
        child = SimpleNamespace(reference=reference, ticket_id=None, active_turn=None)
        monkeypatch.setattr(executor.registry, "authenticate", lambda *_: child)
        original = executor.spool.mint_envelope

        def mint(**kwargs: object) -> QueuedEnvelope:
            entered.set()
            assert release.wait(5)
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(executor.spool, "mint_envelope", mint)
        task = asyncio.create_task(
            executor.handle_ipc(
                IpcRequest(
                    "admitted-write",
                    IpcOperation.write,
                    reference.child_id,
                    "capability",
                    {"operation": "ticket.note", "arguments": {}},
                )
            )
        )
        try:
            while not entered.is_set():
                await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            blocked = await executor.registry.quiesce()
            assert blocked.uncertain_operation_count == 1
            assert not blocked.ready
            # The observer can cancel. Its separate owner must retain the physical thread.
            publication = executor._publications["admitted-write"]
            assert not publication.task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await publication.task
            settled = await executor.registry.quiesce()
            assert settled.uncertain_operation_count == 0
            assert settled.unsent_entry_count == 1
            assert not settled.ready
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if executor._publications:
                await asyncio.gather(*(row.task for row in executor._publications.values()))
            executor.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("is_file", [False, True])
def test_native_absolute_workspace_refuses_missing_directory_or_file_before_child_creation(
    tmp_path: Path, is_file: bool
) -> None:
    async def run() -> None:
        from planner.executor.remote import RemoteBackendChild

        workspace = tmp_path / "native-checkout"
        if is_file:
            workspace.write_text("not a directory")
        spool = FilesystemSpool(tmp_path / "spool")
        created: list[bool] = []

        def factory(
            resolved: ResolvedConversationStart, capability: str, child: ChildReference
        ) -> BackendChildFactory:
            created.append(True)
            return cast(BackendChildFactory, lambda **kwargs: ScriptedChild())

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")
        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=reference.executor_incarnation,
            spool=spool,
            factory=factory,
            workspace_home=tmp_path,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        resolved = ResolvedConversationStart(
            "c_one",
            ConversationBackendKey.codex,
            "test",
            None,
            None,
            workspace,
            ConversationAccess.full,
            workspace_path_policy=WorkspacePathPolicy.native_absolute,
        )
        forwarded: list[ChildCommand] = []

        async def send(command: ChildCommand) -> CommandOutcome:
            forwarded.append(command)
            return await registry.execute_command(command)

        with pytest.raises(BackendSpawnFailed, match="Mac workspace unavailable"):
            await RemoteBackendChild(reference, send, lambda _: canonical_snapshot()).start(
                resolved, vendor_session_cursor=None
            )
        assert "workspace_home_relative" not in forwarded[0].arguments
        assert registry.outcomes[forwarded[0].command_id].disposition is CommandDisposition.refused
        assert created == []
        assert registry.children == {}
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("snapshot_value", [None, {}, {"sources": [], "required_skill_names": []}])
def test_missing_or_invalid_snapshot_refuses_before_provider_creation(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    snapshot_value: object,
) -> None:
    async def run() -> None:
        registry, _spool, native, start = native_registry
        arguments = dict(start.arguments)
        arguments.pop("canonical_skill_snapshot")
        if snapshot_value is not None:
            arguments["canonical_skill_snapshot"] = encode_value(snapshot_value)
        outcome = await registry.execute_command(replace(start, arguments=arguments))
        assert outcome.disposition is CommandDisposition.refused
        assert outcome.result["error_type"] == "BackendSpawnFailed"
        assert "Canonical skill bootstrap failed" in str(outcome.result["error_message"])
        assert native.starts == 0
        assert registry.children == {}
        assert registry.paths is not None
        assert not (Path(os.environ["CODEX_HOME"]) / "skills").exists()

    asyncio.run(run())


def test_native_start_replay_does_not_repeat_skill_bootstrap(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.executor.skills import install_native_skill_snapshot

    installs: list[CanonicalSkillSnapshot] = []
    install = install_native_skill_snapshot

    def counted_install(
        snapshot: CanonicalSkillSnapshot, *, paths: ExecutorPaths, native_home: Path
    ) -> Path:
        installs.append(snapshot)
        return install(snapshot, paths=paths, native_home=native_home)

    monkeypatch.setattr("planner.executor.runtime.install_native_skill_snapshot", counted_install)

    async def run() -> None:
        registry, _spool, native, start = native_registry
        first = await registry.execute_command(start)
        assert first.disposition is CommandDisposition.succeeded
        replay = await registry.execute_command(start)
        assert replay == first
        assert native.starts == 1
        assert installs == [canonical_snapshot()]
        identity = ExecutorIdentity(
            PROTOCOL_VERSION,
            "a" * 40,
            SPOOL_FORMAT_VERSION,
            start.child.executor_incarnation,
            1,
        )
        assert len(registry.inventory(identity).children) == 1
        assert installs == [canonical_snapshot()]

    asyncio.run(run())


def test_claude_bootstrap_uses_ordinary_account_home_when_unconfigured(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    async def run() -> None:
        registry, _spool, native, start = native_registry
        resolved = TypeAdapter(ResolvedConversationStart).validate_python(
            start.arguments["resolved_start"]
        )
        resolved = replace(resolved, backend_key=ConversationBackendKey.claude)
        outcome = await registry.execute_command(
            replace(start, arguments={**start.arguments, "resolved_start": encode_value(resolved)})
        )
        assert outcome.disposition is CommandDisposition.succeeded
        assert native.starts == 1
        for source in canonical_snapshot().sources:
            assert (
                tmp_path / ".claude" / "skills" / source.skill_name / "SKILL.md"
            ).read_bytes() == (source.source_text.encode("utf-8"))

    asyncio.run(run())


@pytest.mark.parametrize("backend", list(ConversationBackendKey))
def test_mac_factory_selects_existing_provider_with_inherited_launch_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: ConversationBackendKey,
) -> None:
    from planner.conversation.production_backends import production_backend_launches

    executor = make_executor(tmp_path / "executor")
    launches = production_backend_launches(panels_server_url="https://unused")
    observed: list[object] = []

    def selected(launch: object, **kwargs: object) -> BackendChildFactory:
        observed.append(launch)
        return cast(BackendChildFactory, object())

    for key, name in (
        (ConversationBackendKey.codex, "CodexAppServerBackendChildFactory"),
        (ConversationBackendKey.hermes, "HermesAcpBackendChildFactory"),
        (ConversationBackendKey.claude, "ClaudeAgentSdkBackendChildFactory"),
    ):

        def unexpected(launch: object) -> BackendChildFactory:
            raise AssertionError("wrong provider factory")

        monkeypatch.setattr(
            "planner.executor.mac." + name, selected if key is backend else unexpected
        )
    monkeypatch.setattr(
        "planner.executor.mac.production_backend_launches", lambda **kwargs: launches
    )
    resolved = ResolvedConversationStart(
        "c_factory",
        backend,
        "test",
        None,
        ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_factory"),)),
        tmp_path,
        ConversationAccess.full,
    )
    reference = ChildReference("child", executor.identity.executor_incarnation, "c_factory")
    try:
        executor._factory("https://unused")(resolved, "native-capability", reference)
        assert len(observed) == 1
        expected = getattr(launches, backend.value)
        actual = observed[0]
        assert actual == replace(
            expected,
            environment_overrides=expected.environment_overrides
            + (
                ("PLAN_EXECUTOR_ROOT", str(executor.paths.root)),
                ("PLAN_EXECUTOR_CHILD_ID", reference.child_id),
                ("PLAN_EXECUTOR_CAPABILITY", "native-capability"),
                ("PLAN_TICKET_FILES_DIR", str(executor.paths.artifacts / "t_factory")),
            ),
        )
    finally:
        executor.spool.close()


def test_absent_stop_requires_exact_identity_settled_prefix_and_preserves_old_outcomes(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        # A command for an absent identity retains its original uncertain result.
        old = replace(start, command_id="old", operation=ChildOperation.steer, arguments={})
        first = await registry.execute_command(old)
        assert first.disposition is CommandDisposition.uncertain
        entered, release = asyncio.Event(), asyncio.Event()
        original_execute = registry._execute

        async def delayed_execute(command: ChildCommand) -> CommandOutcome:
            if command.command_id == "delayed-start":
                entered.set()
                await release.wait()
            return await original_execute(command)

        monkeypatch.setattr(registry, "_execute", delayed_execute)
        delayed = asyncio.create_task(
            registry.execute_command(replace(start, command_id="delayed-start"))
        )
        await entered.wait()
        stop = ChildCommand("cleanup", start.child, ChildOperation.stop, None, {})
        blocked = await registry.execute_command(stop)
        assert blocked.disposition is CommandDisposition.uncertain
        assert start.child.child_id not in registry._retired_children
        release.set()
        assert (await asyncio.wait_for(delayed, 2)).disposition is CommandDisposition.refused
        conflict = replace(
            stop, command_id="conflict", child=replace(start.child, conversation_id="other")
        )
        assert (await registry.execute_command(conflict)).disposition is CommandDisposition.refused
        wrong = replace(
            stop, command_id="wrong", child=replace(start.child, executor_incarnation="old")
        )
        assert (await registry.execute_command(wrong)).disposition is CommandDisposition.refused
        acknowledge_pending(spool)
        registry.confirm_command(old.command_id)
        retry = replace(stop, command_id="retry")
        # Conflicting rejected commands do not become evidence of another assignment.
        assert (await registry.execute_command(retry)).disposition is CommandDisposition.succeeded
        assert await registry.execute_command(old) == first
        assert await registry.execute_command(stop) == blocked
        assert (await registry.execute_command(start)).disposition is CommandDisposition.refused
        for field, value in (("conversation_id", "other"), ("executor_incarnation", "old")):
            invalid = replace(
                old, command_id="invalid-" + field, child=replace(start.child, **{field: value})
            )
            assert (
                await registry.execute_command(invalid)
            ).disposition is CommandDisposition.refused
        acknowledge_pending(spool)
        idempotent = replace(stop, command_id="idempotent")
        assert (
            await registry.execute_command(idempotent)
        ).disposition is CommandDisposition.succeeded
        assert native.starts == 0

    asyncio.run(run())


def test_stop_fences_native_lock_waiter_keeps_final_event_and_refuses_second_owner(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        await registry.execute_command(start)
        child = registry.children[start.child.child_id]
        await child.lock.acquire()
        waiter_entered = asyncio.Event()
        original_execute = registry._execute

        async def execute(command: ChildCommand) -> CommandOutcome:
            if command.command_id == "waiter":
                waiter_entered.set()
            return await original_execute(command)

        monkeypatch.setattr(registry, "_execute", execute)
        waiter = asyncio.create_task(
            registry.execute_command(
                ChildCommand("waiter", start.child, ChildOperation.steer, None, {})
            )
        )
        await waiter_entered.wait()
        stopping, release = asyncio.Event(), asyncio.Event()

        async def stop_native() -> None:
            stopping.set()
            await release.wait()
            assert native.sink is not None
            await native.sink.vendor_session_cursor_rebound("final-cursor")

        monkeypatch.setattr(native, "stop", stop_native)
        stop = ChildCommand("stop", start.child, ChildOperation.stop, None, {})
        task = asyncio.create_task(registry.execute_command(stop))
        # The command waiter must cross the lock after stop owns admission.
        while start.child.child_id not in registry._retiring_children:
            await asyncio.sleep(0)
        child.lock.release()
        await asyncio.wait_for(stopping.wait(), 2)
        assert (await waiter).disposition is CommandDisposition.refused
        second = await registry.execute_command(replace(stop, command_id="second"))
        assert second.disposition is CommandDisposition.refused
        with pytest.raises(PermissionError):
            registry.authenticate(start.child.child_id, child.capability)
        with pytest.raises(PermissionError):
            registry.begin_source_work(start.child)
        release.set()
        assert (await asyncio.wait_for(task, 2)).disposition is CommandDisposition.succeeded
        rows = spool.pending()
        assert rows[-1].payload["command_id"] == "stop"
        assert rows[-2].kind is EnvelopeKind.backend_event
        assert start.child.child_id not in registry.children

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["outcome", "application", "command"])
def test_stop_waits_for_admitted_answer_outer_publication(
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        await registry.execute_command(start)
        assert native.sink is not None
        turn = TurnToken(start.child.conversation_id, 1)
        await native.sink.permission_ask_raised(
            turn, BackendPermissionAsk("ask", "Allow?", None, ())
        )
        ask = registry.children[start.child.child_id].asks["ask"]
        entered, release, stop_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = (
            registry._publish_application
            if boundary == "application"
            else registry._publish_outcome
        )

        async def publish(value: object) -> None:
            entered.set()
            await release.wait()
            await original(value)  # type: ignore[arg-type]

        publication_kind = "application" if boundary == "application" else "outcome"
        monkeypatch.setattr(registry, "_publish_" + publication_kind, publish)
        command = ChildCommand(
            "answer",
            start.child,
            ChildOperation.steer,
            turn,
            {"content": encode_value(text_message_content("late"))},
        )
        publication: asyncio.Task[object]
        if boundary == "command":
            publication = asyncio.create_task(registry.execute_command(command))
        else:
            publication = asyncio.create_task(
                registry.apply_answer(DurableAnswer("answer", ask, "allow", 1))
            )
        await entered.wait()
        registry.confirm_command("answer")
        if boundary != "application":
            assert "answer" in registry._tasks
            assert not registry._tasks["answer"].done()

        async def stop_native() -> None:
            stop_entered.set()

        monkeypatch.setattr(native, "stop", stop_native)
        stop = asyncio.create_task(
            registry.execute_command(
                ChildCommand("stop", start.child, ChildOperation.stop, None, {})
            )
        )
        while start.child.child_id not in registry._retiring_children:
            await asyncio.sleep(0)
        assert not stop_entered.is_set() and not stop.done()
        release.set()
        assert await asyncio.wait_for(publication, 2) is not None
        assert (await asyncio.wait_for(stop, 2)).disposition is CommandDisposition.succeeded
        rows = spool.pending()
        assert rows[-1].payload["command_id"] == "stop"
        if boundary == "command":
            assert rows[-2].kind is EnvelopeKind.command_outcome
            assert native.answers == []
        else:
            assert rows[-2].kind is EnvelopeKind.answer_application
            assert native.answers == [("ask", "allow")]

    asyncio.run(run())


def test_input_revocation_ack_precedes_disk_settlement_without_releasing_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canceled disk thread still owns its descriptor, temp file, and slot."""
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.executor.app_transport import ExecutorAppClient
    from planner.executor.contracts import (
        FileManifest,
        FileScope,
        FileTransfer,
        InputTransferCancel,
        InputTransferRequest,
    )
    from planner.executor.transfers import settled_io
    from planner.executor.wire import encode_message

    async def run() -> None:
        executor = make_executor(tmp_path)
        executor._handshake_accepted = True
        writer = Mock()
        writer.send = AsyncMock()
        executor._writer = writer
        entered, release = threading.Event(), threading.Event()
        temporary: Path | None = None
        received: list[WireMessage] = []

        def disk_work(path: Path) -> None:
            with path.open("wb") as stream:
                stream.write(b"test")
                stream.flush()
                entered.set()
                assert release.wait(5)
                assert path.exists() and not stream.closed

        async def download(slot: object, path: Path, progress: object) -> None:
            nonlocal temporary
            temporary = path
            await settled_io(disk_work, path)

        async def send(message: WireMessage) -> None:
            received.append(message)

        executor._app_client = cast(ExecutorAppClient, SimpleNamespace(download=download))
        monkeypatch.setattr(executor, "_send", send)
        reader = MessageReader()
        slot = FileTransfer(
            "a" * 32,
            "download",
            FileManifest("publication", FileScope.conversation, "c_one", "file", "a" * 64, 4),
        )
        reader.feed_data(
            encode_message(
                WireMessage(
                    WireMessageKind.file_slot,
                    "blocked",
                    as_object(InputTransferRequest("command", slot)),
                )
            )
        )
        reading = asyncio.create_task(executor._read_loop(reader))
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
            reader.feed_data(
                encode_message(
                    WireMessage(
                        WireMessageKind.input_cancel,
                        "blocked",
                        as_object(InputTransferCancel("command", slot.transfer_id)),
                    )
                )
            )
            async with asyncio.timeout(2):
                while not any(frame.kind is WireMessageKind.input_cancelled for frame in received):
                    await asyncio.sleep(0)
            reader.feed_data(
                encode_message(
                    WireMessage(
                        WireMessageKind.input_cancel,
                        "blocked",
                        as_object(InputTransferCancel("command", slot.transfer_id)),
                    )
                )
            )
            async with asyncio.timeout(2):
                while sum(frame.kind is WireMessageKind.input_cancelled for frame in received) < 2:
                    await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert executor._input_transfers["blocked"].revoked
            assert executor._transfers == 1 and executor._transfer_capacity._value == 3
            assert temporary is not None and temporary.exists()
            assert not executor._input_workers["blocked"].done()
            # Three unrelated reservations exhaust the four-transfer capacity.
            for _ in range(3):
                await executor._transfer_capacity.acquire()
            waiting = asyncio.create_task(executor._transfer_capacity.acquire())
            await asyncio.sleep(0)
            assert not waiting.done()
            release.set()
            await asyncio.wait_for(waiting, 2)
            async with asyncio.timeout(2):
                while executor._input_transfers:
                    await asyncio.sleep(0)
            assert not temporary.exists() and executor._transfers == 0
            assert not writer.send.called
        finally:
            release.set()
            reader.feed_eof()
            await asyncio.gather(reading, return_exceptions=True)
            await asyncio.gather(*executor._transfer_tasks, return_exceptions=True)
            executor.spool.close()

    asyncio.run(run())


def test_input_progress_socket_failure_closes_original_writer_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.executor.app_transport import ExecutorAppClient
    from planner.executor.contracts import (
        FileManifest,
        FileScope,
        FileTransfer,
        InputTransferRequest,
    )

    async def run() -> None:
        executor = make_executor(tmp_path)
        executor._handshake_accepted = True
        original, replacement = Mock(), Mock()

        async def fail_drain(*_: object) -> None:
            executor._writer = replacement
            raise OSError("control socket failed")

        original.send = AsyncMock(side_effect=fail_drain)
        replacement.send = AsyncMock()
        executor._writer = original

        async def download(slot: object, path: Path, progress: object) -> None:
            from collections.abc import Awaitable, Callable

            await cast(Callable[[str, int], Awaitable[None]], progress)("download", 1)

        executor._app_client = cast(ExecutorAppClient, SimpleNamespace(download=download))
        slot = FileTransfer(
            "a" * 32,
            "download",
            FileManifest("publication", FileScope.conversation, "c_one", "file", "a" * 64, 1),
        )
        try:
            await executor._input(
                WireMessage(
                    WireMessageKind.file_slot,
                    "input",
                    as_object(InputTransferRequest("command", slot)),
                )
            )
            original.close.assert_called_once_with()
            replacement.close.assert_not_called()
            replacement.send.assert_not_called()
            assert not executor._input_transfers and executor._transfers == 0
        finally:
            executor.spool.close()

    asyncio.run(run())


def test_changed_capture_replay_reuses_receipts_after_lost_envelope_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from planner.executor.app_transport import ExecutorAppClient
    from planner.executor.contracts import AcceptanceReceipt, FileManifest, ReceiptDisposition

    async def run() -> None:
        executor = make_executor(tmp_path)
        executor._handshake_accepted = True
        child = ChildReference(executor.identity.executor_incarnation, "child", "c_one")
        directory = executor.paths.artifacts / "t_one"
        directory.mkdir(parents=True)
        (directory / "image").write_bytes(b"A")

        def mint() -> QueuedEnvelope:
            return executor.spool.mint_ticket_envelope(
                paths=executor.paths, ticket_id="t_one", child=child,
                kind=EnvelopeKind.cli_write, payload={"child": as_object(child)},
            )

        first, second = mint(), mint()
        accepted: set[str] = set()
        manifests: list[str] = []
        uploads: list[bytes] = []
        envelopes: list[str] = []
        lost = False

        async def upload(slot: object, path: Path) -> None:
            uploads.append(path.read_bytes())

        async def transport(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            nonlocal lost
            if kind in (WireMessageKind.file_manifest, WireMessageKind.file_complete):
                manifest = TypeAdapter(FileManifest).validate_python(payload["manifest"])
                if kind is WireMessageKind.file_manifest:
                    manifests.append(manifest.publication_id)
                    if manifest.publication_id not in accepted:
                        return WireMessage(WireMessageKind.file_slot, "slot", {
                            "transfer_id": "upload", "direction": "upload",
                            "manifest": payload["manifest"],
                        })
                else:
                    assert uploads == [b"A"]
                    accepted.add(manifest.publication_id)
                return WireMessage(WireMessageKind.file_accepted, "accepted", as_object(manifest))
            row = TypeAdapter(QueuedEnvelope).validate_python(payload)
            assert all(item.publication_id in accepted for item in row.file_dependencies)
            envelopes.append(row.submission_id)
            if not lost:
                lost = True
                raise ConnectionError("lost envelope receipt")
            return WireMessage(WireMessageKind.receipt, "receipt", as_object(AcceptanceReceipt(
                row.sender_id, row.submission_id, row.payload_sha256,
                ReceiptDisposition.accepted, {}, 1,
            )))

        executor._app_client = cast(ExecutorAppClient, SimpleNamespace(upload=upload))
        monkeypatch.setattr(executor, "_request", transport)
        try:
            with pytest.raises(ConnectionError, match="lost envelope"):
                await executor._replay_envelope(first)
            assert executor.spool.pending_count() == 2
            await executor._replay_envelope(first)
            await executor._replay_envelope(second)
            assert manifests == [first.file_dependencies[0].publication_id] * 2
            assert uploads == [b"A"]
            assert envelopes == [first.submission_id, first.submission_id, second.submission_id]
            assert mint().file_dependencies == ()
            assert executor.spool.pending_count() == 1
        finally:
            executor.spool.close()

    asyncio.run(run())
