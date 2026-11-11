"""Native callback continuity is tested against the actual executor registry."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    PermissionAskRaised,
    PromptWriteFailed,
    PromptWriteRefused,
    SessionLoadFailed,
    ToolCallFinished,
    TurnEnded,
    TurnToken,
    VendorSessionCursorRebound,
)
from planner.conversation.contracts import (
    AnswerFate,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import ConversationTurnEnding, ToolCallStatus
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.execution_identity import ChildReference
from planner.executor.configuration import ExecutorPaths
from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.mac_runner import MacRunner
from planner.executor.mac_service import MacService
from planner.executor.protocol import (
    AcceptanceReceipt,
    AnswerApplication,
    BackendEvent,
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    DurableAnswer,
    EnvelopeKind,
    IpcOperation,
    IpcRequest,
    QueuedEnvelope,
    ReceiptDisposition,
    StartRun,
    StopRun,
    WritePrompt,
    as_object,
    decode_command_arguments,
    decode_event_arguments,
    encode_value,
    type_adapter,
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
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
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

    async def answer_permission_ask(
        self, ask_id: str, option_id: str, **kwargs: object
    ) -> AnswerFate:
        self.answers.append((ask_id, option_id))
        await asyncio.sleep(0)
        return AnswerFate.applied

    @property
    def stop_outside_lock(self) -> bool:
        return False

    async def withdraw_delivery(self) -> bool:
        return True

    async def detach(self) -> None:
        await self.stop()

    async def stop(self) -> None:
        return None


@pytest.fixture
def native_registry(
    tmp_path: Path,
) -> Iterator[tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand]]:
    spool = MacOutbox(tmp_path / "spool")
    native = ScriptedChild()
    reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")

    def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
        def create(**kwargs: object) -> BackendChild:
            native.sink = cast(BackendEventSink, kwargs["event_sink"])
            return cast(BackendChild, native)

        return cast(BackendChildFactory, create)

    registry = MacRunner(
        paths=ExecutorPaths(tmp_path / "executor"),
        incarnation=reference.executor_incarnation,
        outbox=spool,
        factory=factory,
        message_files=ConversationMessageFiles(tmp_path / "unused"),
    )
    resolved = ResolvedConversationStart(
        "c_one",
        ConversationBackendKey.codex,
        "test",
        None,
        ConversationRoleMaterials(
            "worker",
            (
                ("PLAN_ACTOR", "worker"),
                ("PLAN_TICKET_ID", "t_one"),
                ("PLAN_TICKET_WORKSPACE_MANAGED", "1"),
            ),
        ),
        tmp_path,
        ConversationAccess.full,
    )
    start = ChildCommand(
        "start",
        reference,
        ChildOperation.start,
        None,
        decode_command_arguments(
            ChildOperation.start,
            {
                "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                "resolved_start": encode_value(resolved),
            },
        ),
    )
    try:
        yield registry, spool, native, start
    finally:
        spool.close()


def test_legacy_native_resume_keeps_original_checkout_and_uncommitted_work(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old native identity has no managed-workspace contract; resume its actual cwd."""

    async def run() -> None:
        registry, _, native, start = native_registry
        original = tmp_path / "mac-home" / "projects" / "wren"
        original.mkdir(parents=True)
        subprocess.run(["git", "init", str(original)], check=True, capture_output=True)
        source = original / "worker-change.txt"
        source.write_text("uncommitted work")
        registry.workspace_home = tmp_path / "mac-home"
        assert isinstance(start.arguments, StartRun)
        resolved = replace(
            start.arguments.resolved_start,
            role_materials=ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_one"),)),
            workspace_folder=Path("/home/vps/projects/wren"),
            workspace_path_policy=WorkspacePathPolicy.home_relative,
        )
        seen: list[Path] = []

        async def resume(
            resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
        ) -> None:
            assert vendor_session_cursor == "legacy-session"
            assert (resolved_start.workspace_folder / source.name).read_text() == "uncommitted work"
            seen.append(resolved_start.workspace_folder)

        monkeypatch.setattr(native, "start", resume)
        start = replace(
            start,
            arguments=replace(
                start.arguments,
                resolved_start=resolved,
                vendor_session_cursor="legacy-session",
                workspace_home_relative="projects/wren",
            ),
        )
        assert (await registry.run(start)).disposition is CommandDisposition.succeeded
        assert seen == [original]
        ticket_root = registry.paths.artifacts / "t_one"
        assert not (ticket_root / "worktree").exists()
        assert not (ticket_root / ".panels-workspace.json").exists()
        assert source.read_text() == "uncommitted work"

    asyncio.run(run())


def test_same_ticket_starts_recheck_occupancy_after_workspace_lock(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
) -> None:
    """Two starts can both pass the fast check while cleanup holds the local lock."""

    async def run() -> None:
        registry, _, native, start = native_registry
        both_waiting = asyncio.Event()

        class ObservedLock(asyncio.Lock):
            waiting = 0

            async def acquire(self) -> Literal[True]:
                if self.locked():
                    self.waiting += 1
                    if self.waiting == 2:
                        both_waiting.set()
                return await super().acquire()

        lock = ObservedLock()
        registry._workspace_lock = lock
        await lock.acquire()
        other = replace(
            start,
            command_id="second-start",
            child=replace(start.child, child_id=str(uuid4())),
        )
        first = asyncio.create_task(registry.run(start))
        second = asyncio.create_task(registry.run(other))
        try:
            await asyncio.wait_for(both_waiting.wait(), timeout=5)
        finally:
            lock.release()
        outcomes = await asyncio.gather(first, second)
        assert [outcome.disposition for outcome in outcomes] == [
            CommandDisposition.succeeded,
            CommandDisposition.refused,
        ]
        assert native.starts == 1
        assert len(registry._children) == 1

    asyncio.run(run())


def test_workspace_cleanup_stops_idle_retained_child_but_preserves_an_active_turn(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gap: an idle native session survives Ticket end and otherwise defeats expiry."""
    from planner.ticket_workspaces.contracts import RETENTION_SECONDS, WorkspaceState

    async def run() -> None:
        registry, spool, native, start = native_registry
        assert isinstance(start.arguments, StartRun)
        resolved = replace(
            start.arguments.resolved_start,
            role_materials=ConversationRoleMaterials(
                "worker",
                (
                    ("PLAN_TICKET_ID", "t_01234567"),
                    ("PLAN_TICKET_WORKSPACE_MANAGED", "1"),
                ),
            ),
        )
        start = replace(start, arguments=replace(start.arguments, resolved_start=resolved))
        grants: list[WorkspaceState] = []
        stops: list[bool] = []

        async def grant(state: WorkspaceState, workspace: object) -> bool:
            grants.append(state)
            return True

        async def stop() -> None:
            stops.append(True)

        monkeypatch.setattr(registry, "_retire_workspace", grant)
        monkeypatch.setattr(native, "stop", stop)
        assert (await registry.run(start)).disposition is CommandDisposition.succeeded
        turn = TurnToken("turn", 1)
        prompt = ChildCommand(
            "prompt",
            start.child,
            ChildOperation.write_prompt,
            turn,
            WritePrompt(
                content=text_message_content("work"),
                sender_content=(),
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
            ),
        )
        assert (await registry.run(prompt)).disposition is CommandDisposition.succeeded
        state = WorkspaceState("t_01234567", 1, False)
        folder = registry.paths.artifacts / state.ticket_id
        await registry.cleanup_workspaces((state,), RETENTION_SECONDS + 2)
        assert folder.exists() and not grants and not stops
        assert native.sink is not None
        await native.sink.report(turn, TurnEnded(ending=ConversationTurnEnding.completed))
        await registry.cleanup_workspaces((state,), RETENTION_SECONDS + 2)
        assert not folder.exists() and stops == [True] and grants == [state]
        proofs = [row for row in spool.pending() if row.kind is EnvelopeKind.child_stopped]
        assert len(proofs) == 1
        assert start.child.child_id not in registry._children

    asyncio.run(run())


def acknowledge_pending(spool: MacOutbox) -> None:
    for row in spool.pending():
        spool.acknowledge(
            AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                ReceiptDisposition.accepted,
                {},
                1,
            )
        )


@pytest.mark.parametrize(
    "trigger",
    [
        "symlink",
        "fifo",
        "percent_name",
        "backslash_name",
        "vanished",
        "permission",
        "changing",
        "detail",
    ],
)
def test_bad_ticket_entry_does_not_lose_event_or_stop(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    trigger: str,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        assert isinstance(start.arguments, StartRun)
        resolved = replace(
            start.arguments.resolved_start,
            role_materials=ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_one"),)),
        )
        start = replace(
            start,
            arguments=replace(
                start.arguments,
                resolved_start=resolved,
            ),
        )
        assert (await registry.run(start)).disposition is CommandDisposition.succeeded
        assert native.sink is not None
        folder = ExecutorPaths(spool.root.parent).artifacts / "t_one"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "good.txt").write_text("good")
        bad = folder / "bad"
        if trigger == "symlink":
            bad.symlink_to(folder / "good.txt")
        elif trigger == "fifo":
            os.mkfifo(bad)
        elif trigger in ("percent_name", "backslash_name"):
            bad = folder / ("a%2Eb" if trigger == "percent_name" else "a\\b")
            bad.write_text("skip")
        elif trigger in ("vanished", "permission"):
            bad.write_text("vanish")
            original_open = os.open

            def vanish(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if path == "bad":
                    if trigger == "permission":
                        raise PermissionError("source unreadable")
                    bad.unlink(missing_ok=True)
                return original_open(path, flags, mode, dir_fd=dir_fd)

            monkeypatch.setattr(os, "open", vanish)
        elif trigger == "changing":
            bad.write_text("changing")
            inode = bad.stat().st_ino
            original_read = os.read

            def append(descriptor: int, count: int) -> bytes:
                block = original_read(descriptor, count)
                if block and os.fstat(descriptor).st_ino == inode:
                    with bad.open("ab") as file:
                        file.write(b"more")
                    # Change once per capture, so the read still reaches EOF.
                    monkeypatch.setattr(os, "read", original_read)
                return block

            monkeypatch.setattr(os, "read", append)
        detail = "x" * (1024 * 1024 + 1) if trigger == "detail" else "ordinary detail"
        await native.sink.report(
            TurnToken("turn", 1),
            ToolCallFinished(
                tool_call_id="tool", tool_call_status=ToolCallStatus.completed, detail=detail
            ),
        )
        events = [row for row in spool.pending() if row.kind is EnvelopeKind.backend_event]
        assert len(events) == 1
        assert as_object(events[0].payload["arguments"])["detail"] == (
            "x" * 65536 if trigger == "detail" else detail
        )
        assert [file.relative_path for file in events[0].file_dependencies] == ["good.txt"]
        if trigger != "detail":
            assert "Skipping" in caplog.text and bad.name in caplog.text

        # The gateway write and retry path is proved in test_executor_ipc_publication.
        stopped = await registry.run(
            ChildCommand("stop", start.child, ChildOperation.stop, None, StopRun())
        )
        assert stopped.disposition is CommandDisposition.succeeded
        assert any(row.kind is EnvelopeKind.child_stopped for row in spool.pending())

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["incarnation", "quiescence"])
def test_pre_dispatch_refusal_never_calls_native(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    boundary: str,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        if boundary == "incarnation":
            start = replace(start, child=replace(start.child, executor_incarnation=str(uuid4())))
        else:
            await registry.quiesce()
        assert (await registry.run(start)).disposition is CommandDisposition.refused
        assert native.starts == native.prompts == 0
        assert spool.pending() == ()

    asyncio.run(run())


class _LocalSessionLoadRefused(SessionLoadFailed):
    """The typed backend contract includes subclasses, independent of class spelling."""


@pytest.mark.parametrize(
    ("failure", "operation", "expected"),
    [
        (_LocalSessionLoadRefused("no session"), ChildOperation.start, CommandDisposition.refused),
        (
            PromptWriteRefused("not emitted"),
            ChildOperation.write_prompt,
            CommandDisposition.refused,
        ),
        (
            PromptWriteFailed("possibly emitted"),
            ChildOperation.write_prompt,
            CommandDisposition.uncertain,
        ),
    ],
)
def test_native_typed_refusal_is_distinct_from_possible_emission(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    operation: ChildOperation,
    expected: CommandDisposition,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command = start
        if operation is ChildOperation.write_prompt:
            assert (await registry.run(start)).disposition is CommandDisposition.succeeded
            command = ChildCommand(
                "typed-prompt",
                start.child,
                operation,
                TurnToken("c_one", 1),
                decode_command_arguments(
                    operation,
                    {
                        "content": encode_value(text_message_content("once")),
                        "sender_content": encode_value(text_message_content("once")),
                        "sender_label": "Owner",
                        "mode": "queue",
                    },
                ),
            )
        calls = 0

        async def fail(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            raise failure

        monkeypatch.setattr(native, operation.value, fail)
        outcome = await registry.run(command)
        assert outcome.disposition is expected
        if isinstance(failure, _LocalSessionLoadRefused):
            assert outcome.result["error_type"] == "SessionLoadFailed"
        assert registry._commands[command.command_id].outcome == outcome
        assert await registry.run(command) == outcome
        assert calls == 1
        if operation is not ChildOperation.write_prompt:
            assert spool.pending() == ()
            return
        # R1: a failed prompt ends its turn failed, once, under a fixed identity, so a
        # repeated run publishes nothing new.
        (failed,) = spool.pending()
        assert failed.submission_id == (
            "turn_failed_" + hashlib.sha256(command.command_id.encode()).hexdigest()
        )
        ending = decode_event_arguments(type_adapter(BackendEvent).validate_python(failed.payload))
        assert isinstance(ending, TurnEnded) and ending.ending is ConversationTurnEnding.failed
        assert str(ending.error_summary).startswith(
            "The Mac could not deliver this message: "
            if expected is CommandDisposition.refused
            else "The Mac lost track of this message; it may have reached the agent: "
        )

    asyncio.run(run())


def test_native_error_with_admission_text_stays_uncertain_after_confirmation_and_drain(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry

        async def failing_start(
            resolved_start: ResolvedConversationStart,
            *,
            vendor_session_cursor: str | None,
        ) -> None:
            native.starts += 1
            raise ValueError("executor is quiescing")

        monkeypatch.setattr(native, "start", failing_start)
        outcome = await registry.run(start)
        assert outcome.disposition == CommandDisposition.uncertain
        assert outcome.result == {
            "error_type": "ValueError",
            "error_message": "executor is quiescing",
        }
        assert native.starts == 1
        assert spool.pending() == ()

        acknowledge_pending(spool)
        assert registry._commands[start.command_id].outcome == outcome
        blocked = await registry.quiesce()
        assert blocked.active_turn_count == 0
        assert blocked.pending_callback_count == 0
        assert blocked.unsent_entry_count == 0
        assert blocked.uncertain_operation_count == 1
        assert not blocked.ready
        assert await registry.run(start) == outcome
        assert native.starts == 1

    asyncio.run(run())


async def uncertain_automatic_compaction(
    registry: MacRunner,
    native: ScriptedChild,
    start: ChildCommand,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ChildCommand, CommandOutcome]:
    assert (await registry.run(start)).disposition == CommandDisposition.succeeded

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
        decode_command_arguments(
            ChildOperation.write_prompt,
            {
                "content": encode_value(text_message_content("continue")),
                "sender_content": encode_value(text_message_content("continue")),
                "sender_label": "Owner",
                "mode": PromptDeliveryMode.queue.value,
                "automatic_compaction": True,
            },
        ),
    )
    outcome = await registry.run(command)
    assert outcome.disposition == CommandDisposition.uncertain
    return command, outcome


def test_confirmed_exact_stop_allows_exit_without_rewriting_uncertain_compaction(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, first = await uncertain_automatic_compaction(registry, native, start, monkeypatch)
        stop = ChildCommand(
            "stop",
            start.child,
            ChildOperation.stop,
            None,
            decode_command_arguments(ChildOperation.stop, {}),
        )
        assert (await registry.run(stop)).disposition == CommandDisposition.succeeded
        assert start.child.child_id not in registry._children

        blocked = await registry.quiesce()
        assert blocked.uncertain_operation_count == 0
        assert blocked.unsent_entry_count > 0
        assert not blocked.ready
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        assert registry._commands[command.command_id].outcome == first
        assert await registry.run(command) == first
        with pytest.raises(ValueError, match="different content"):
            await registry.run(replace(command, turn=TurnToken(command.child.conversation_id, 999)))
        assert (native.starts, native.prompts) == (1, 1)
        assert spool.pending_count() == 0

    asyncio.run(run())


def test_failed_stop_keeps_uncertainty_even_after_output_is_acknowledged(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        command, first = await uncertain_automatic_compaction(registry, native, start, monkeypatch)

        async def failed_stop() -> None:
            raise RuntimeError("termination not confirmed")

        monkeypatch.setattr(native, "stop", failed_stop)
        stopped = await registry.run(
            ChildCommand(
                "stop",
                start.child,
                ChildOperation.stop,
                None,
                decode_command_arguments(ChildOperation.stop, {}),
            )
        )
        assert stopped.disposition is CommandDisposition.uncertain
        acknowledge_pending(spool)
        assert not (await registry.quiesce()).ready
        assert await registry.run(command) == first
        assert (native.starts, native.prompts) == (1, 1)

    asyncio.run(run())


def test_unknown_child_cannot_write_to_executor(tmp_path: Path) -> None:
    async def run() -> None:
        executor = MacService(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        forbidden = await executor.gateway.handle(
            IpcRequest(
                "bad-child",
                IpcOperation.write,
                "unknown",
                "forged",
                {"operation": "ticket.note", "arguments": {}},
            )
        )
        assert forbidden.error is not None
        assert executor.outbox.pending() == ()
        executor.outbox.close()

    asyncio.run(run())


def test_quiesce_waits_for_start_and_rechecks_stop_emitted_journal(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        entered, released = asyncio.Event(), asyncio.Event()
        native_stops: list[bool] = []

        async def starting(*args: object, **kwargs: object) -> None:
            entered.set()
            await released.wait()

        async def stop() -> None:
            native_stops.append(True)
            assert native.sink is not None
            await native.sink.report(
                None, VendorSessionCursorRebound(vendor_session_cursor="cursor-at-stop")
            )

        monkeypatch.setattr(native, "start", starting)
        monkeypatch.setattr(native, "stop", stop)
        task = asyncio.create_task(registry.run(start))
        await entered.wait()
        assert not (await registry.quiesce()).ready
        assert native_stops == []
        released.set()
        await task
        source_release = asyncio.Event()
        source_task = registry.source_work(start.child, source_release.wait)
        assert not (await registry.quiesce()).ready
        assert native_stops == []
        source_release.set()
        await source_task
        assert not (await registry.quiesce()).ready
        assert native_stops == [True]
        assert len(spool.pending()) == 1
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready

    asyncio.run(run())


@pytest.mark.parametrize("publication_fails", [False, True])
def test_cancelled_event_caller_leaves_stop_waiting_for_publication(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    publication_fails: bool,
) -> None:
    async def run() -> None:
        registry, outbox, native, start = native_registry
        await registry.run(start)
        assert native.sink is not None
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        add = outbox.add

        def held_event(entry: OutboxEntry, *, ticket_id: str | None = None) -> QueuedEnvelope:
            if entry.kind is EnvelopeKind.backend_event:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                if publication_fails:
                    raise OSError("event could not reach disk")
            return add(entry, ticket_id=ticket_id)

        monkeypatch.setattr(outbox, "add", held_event)
        event = asyncio.create_task(
            native.sink.report(
                None, VendorSessionCursorRebound(vendor_session_cursor="saved-cursor")
            )
        )
        stopping = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            event.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(event, 1)
            stopping = asyncio.create_task(
                registry.run(
                    ChildCommand("stop", start.child, ChildOperation.stop, None, StopRun())
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(stopping), 0.05)
            release.set()
            outcome = await asyncio.wait_for(stopping, 5)
            if publication_fails:
                assert outcome.disposition is CommandDisposition.uncertain
                assert not outbox.pending()
            else:
                assert outcome.disposition is CommandDisposition.succeeded
                assert [entry.kind for entry in outbox.pending()] == [
                    EnvelopeKind.backend_event,
                    EnvelopeKind.child_stopped,
                ]
        finally:
            release.set()
            await asyncio.gather(event, return_exceptions=True)
            if stopping is not None:
                await stopping

    asyncio.run(run())


def test_applied_answer_recovers_when_outcome_fsync_fails(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:

        registry, spool, native, start = native_registry
        reference = start.child
        await registry.run(start)
        assert native.sink is not None
        turn = TurnToken("c_one", 1)
        await native.sink.report(
            turn, PermissionAskRaised(ask=BackendPermissionAsk("ask", "Allow?", None, ()))
        )
        ask = next(iter(next(iter(registry._children.values())).asks.values()))
        answer = DurableAnswer("answer", ask, "allow", 1)
        original = spool.add
        failed = False

        def fail_outcome(entry: OutboxEntry, *, ticket_id: str | None = None) -> QueuedEnvelope:
            kind, payload = entry.kind, entry.payload
            nonlocal failed
            if (
                kind == EnvelopeKind.answer_application
                and payload.get("answer_id") == "answer"
                and not failed
            ):
                failed = True
                raise OSError("disk failed after callback")
            return original(entry, ticket_id=ticket_id)

        monkeypatch.setattr(spool, "add", fail_outcome)
        outer = ChildCommand(
            answer.answer_id,
            reference,
            ChildOperation.answer_permission_ask,
            turn,
            decode_command_arguments(
                ChildOperation.answer_permission_ask,
                {"ask_id": "ask", "option_id": "allow", "answer": encode_value(answer)},
            ),
        )
        with pytest.raises(OSError, match="after callback"):
            await registry.run(outer)
        monkeypatch.setattr(spool, "add", original)
        await registry.run(outer)
        assert native.answers == [("ask", "allow")]
        assert next(iter(registry._children.values())).asks == {}
        assert not (await registry.quiesce()).ready
        outcome = await registry.run(outer)
        assert outcome.disposition == CommandDisposition.succeeded
        assert "answer_application" in outcome.result
        assert await registry.run(outer) == outcome
        recovered = TypeAdapter(AnswerApplication).validate_python(
            outcome.result["answer_application"]
        )
        assert native.answers == [("ask", "allow")]
        assert outcome.result["answer_application"] == encode_value(recovered)
        assert any(row.kind == EnvelopeKind.answer_application for row in spool.pending())

    asyncio.run(run())


def test_real_daemon_controlled_quiesce_exits_zero(executor_transport_home: Path) -> None:
    import socket
    import subprocess
    import sys

    from planner.executor.protocol import IpcResponse, encode_frame

    root = executor_transport_home / "daemon"
    script = (
        "import asyncio; from pathlib import Path; "
        "from planner.executor.mac_service import MacService; "
        "from planner.executor.configuration import ExecutorPaths; "
        f"service=MacService(paths=ExecutorPaths(Path({str(root)!r})),revision={'a' * 40!r},"
        "executor_id='owner-mac',panels_server_url='https://127.0.0.1:1'); "
        "asyncio.run(service.run()); "
        "assert service.gateway.exit_committed"
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
            assert process.poll() is None, process.communicate(timeout=1)
            assert time.monotonic() < deadline
            time.sleep(0.01)
        health = exchange(IpcRequest("health", IpcOperation.health, None, None, {}))
        assert health.result is not None

        def quiesce(*arguments: str) -> dict[str, object]:
            result = subprocess.run(
                [str(Path(sys.executable).with_name("panels")), "executor", "quiesce", *arguments],
                env={**os.environ, "PLAN_EXECUTOR_ROOT": str(root)},
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
            )
            return cast(dict[str, object], json.loads(result.stdout))

        receipt = quiesce()
        assert receipt["exit_committed"] is False
        assert process.poll() is None
        receipt = quiesce("--exit-when-ready")
        assert receipt["identity"] == health.result["identity"]
        assert receipt["process_id"] == process.pid
        assert receipt["exit_committed"] is True
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
        if policy is WorkspacePathPolicy.absolute:
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
        if policy is WorkspacePathPolicy.executor_default:
            # A Project start names its folder inside the Mac workspace, never a VPS path.
            configured_workspace = Path("/projects/project_panels")
            workspace = paths.workspace / "projects" / "project_panels"
            assert not paths.workspace.exists()
        spool = MacOutbox(paths.spool)
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
                assert resolved_start.role_materials is not None
                assert resolved_start.role_materials.role_text.startswith("worker\n\n")
                assert (
                    "Panels owns this Ticket workspace" in resolved_start.role_materials.role_text
                )
                environment = dict(resolved_start.role_materials.identity_environment_variables)
                generation_skills = Path(environment["PLAN_NATIVE_SKILLS_DIR"])
                assert (generation_skills / "panels" / "SKILL.md").read_bytes() == (
                    native_home / "skills" / "panels" / "SKILL.md"
                ).read_bytes()
                (Path(environment["PLAN_TICKET_FILES_DIR"]) / "proof.txt").write_text(
                    "native artifact"
                )

        native = InspectingChild()

        def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
            # Bootstrap is complete before provider construction.
            for source in canonical_snapshot().sources:
                assert (native_home / "skills" / source.skill_name / "SKILL.md").read_bytes() == (
                    source.source_text.encode("utf-8")
                )
            constructed.append(resolved)
            return cast(BackendChildFactory, lambda **kwargs: native)

        reference = ChildReference(str(uuid4()), str(uuid4()), "c_one")
        registry = MacRunner(
            incarnation=reference.executor_incarnation,
            outbox=spool,
            factory=factory,
            paths=paths,
            workspace_home=mac_home,
            message_files=ConversationMessageFiles(tmp_path / "unused"),
        )
        remote_identity = (
            ("PLAN_TICKET_ID", "t_own"),
            ("PLAN_TICKET_WORKSPACE_MANAGED", "1"),
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
        from tests.support.executor_turns import scripted_remote_child

        async def send(command: ChildCommand) -> CommandOutcome:
            outcome = await registry.run(command)
            assert outcome.disposition is CommandDisposition.succeeded
            return outcome

        # Exercise VPS encoding and Mac hydration with distinct home directories.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: vps_home))
        remote = scripted_remote_child(reference, send, lambda start: canonical_snapshot())
        await remote.start(resolved, vendor_session_cursor="retained-native-session")
        assert received == constructed
        assert (paths.artifacts / "t_own" / "proof.txt").read_text() == "native artifact"
        ticket_workspace = paths.artifacts / "t_own" / "worktree"
        assert received[0].workspace_folder == ticket_workspace
        assert received[0].workspace_path_policy is WorkspacePathPolicy.absolute
        if policy is WorkspacePathPolicy.executor_default:
            assert workspace.is_dir()
            assert not (mac_home / "projects" / "project_panels").exists()
            marker = ticket_workspace / "retained.txt"
            marker.write_text("keep")
            for subfolder, _expected in (
                ("projects/project_panels", workspace),
                (None, paths.workspace),
            ):
                localized = registry._localize_start(
                    replace(resolved, workspace_folder=Path("/")),
                    reference,
                    "capability",
                    subfolder,
                    {"PLAN_TICKET_ID": "t_own", "PLAN_TICKET_WORKSPACE_MANAGED": "1"},
                    paths.skills,
                )
                assert localized.workspace_folder == ticket_workspace.resolve()
            assert marker.read_text() == "keep"
        assert received[0].role_materials is not None
        identity = dict(received[0].role_materials.identity_environment_variables)
        assert identity["PLAN_TICKET_FILES_DIR"] == str(paths.artifacts / "t_own")
        assert identity["PLAN_EXECUTOR_ROOT"] == str(paths.root)
        assert identity["PLAN_EXECUTOR_CHILD_ID"] == reference.child_id
        assert identity["PLAN_EXECUTOR_CAPABILITY"] != "forged-capability"
        assert not {"HOME", "CODEX_HOME", "HERMES_HOME", "CLAUDE_CONFIG_DIR"} & identity.keys()
        assert identity["PLAN_NATIVE_SKILLS_DIR"] != str(vps_home / "forged-skills")
        assert os.environ["HOME"] == str(mac_home)
        assert os.environ["CODEX_HOME"] == str(mac_home / ".codex")
        spool.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("policy", "relative"),
    [
        (WorkspacePathPolicy.home_relative, "projects/missing"),
        (WorkspacePathPolicy.home_relative, "../escape"),
        (WorkspacePathPolicy.executor_default, "../escape"),
        (WorkspacePathPolicy.absolute, None),
    ],
)
def test_unavailable_or_unsafe_workspace_refuses_before_native_creation(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path,
    policy: WorkspacePathPolicy,
    relative: str | None,
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        registry.workspace_home = tmp_path
        unavailable = tmp_path / "not-a-directory"
        unavailable.write_text("file")
        assert isinstance(start.arguments, StartRun)
        resolved = replace(
            start.arguments.resolved_start,
            workspace_folder=unavailable,
            workspace_path_policy=policy,
        )
        command = replace(
            start,
            arguments=replace(
                start.arguments,
                resolved_start=resolved,
                workspace_home_relative=relative,
            ),
        )
        outcome = await registry.run(command)
        assert outcome.disposition is CommandDisposition.refused
        assert outcome.result["error_type"] == "BackendSpawnFailed"
        assert native.starts == 0
        assert registry._children == {}
        assert not (registry.paths.workspace.parent / "escape").exists()

    asyncio.run(run())


def test_native_start_requires_ticket_identity(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
) -> None:
    async def run() -> None:
        registry, _, native, start = native_registry
        assert isinstance(start.arguments, StartRun)
        command = replace(
            start,
            arguments=replace(
                start.arguments,
                resolved_start=replace(start.arguments.resolved_start, role_materials=None),
            ),
        )
        outcome = await registry.run(command)
        assert outcome.disposition is CommandDisposition.refused
        assert outcome.result["error_type"] == "BackendSpawnFailed"
        assert outcome.result["error_message"] == "Mac runs require a Ticket identity"
        assert native.starts == 0
        assert registry._children == {}

    asyncio.run(run())


def test_absent_stop_orders_proof_after_output_and_preserves_old_outcomes(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
) -> None:
    async def run() -> None:
        registry, spool, native, start = native_registry
        old = replace(
            start,
            command_id="old",
            operation=ChildOperation.steer,
            turn=TurnToken(start.child.conversation_id, 1),
            arguments=decode_command_arguments(
                ChildOperation.steer,
                {"content": encode_value(text_message_content("test")), "sender_label": "Owner"},
            ),
        )
        first = await registry.run(old)
        # This process never started the child, so nothing reached an agent.
        assert first.disposition is CommandDisposition.refused
        assert first.result["reason"] == "child_start_failed"
        earlier = spool.add(
            OutboxEntry(start.child.conversation_id, EnvelopeKind.backend_event, {})
        )
        stop = ChildCommand("cleanup", start.child, ChildOperation.stop, None, StopRun())
        wrong = replace(
            stop, command_id="wrong", child=replace(start.child, executor_incarnation="old")
        )
        refused = await registry.run(wrong)
        assert refused.disposition is CommandDisposition.refused
        assert refused.result == {"reason": "incarnation_gone"}
        stopped = await registry.run(stop)
        assert stopped.disposition is CommandDisposition.succeeded
        pending = spool.pending()
        assert [row.kind for row in pending] == [
            EnvelopeKind.backend_event,
            EnvelopeKind.child_stopped,
        ]
        assert pending[0] == earlier
        assert await registry.run(old) == first
        assert await registry.run(stop) == stopped
        assert spool.pending() == pending
        assert (await registry.run(start)).disposition is CommandDisposition.refused
        assert not (await registry.quiesce()).ready
        acknowledge_pending(spool)
        assert (await registry.quiesce()).ready
        assert native.starts == 0

    asyncio.run(run())


@pytest.mark.parametrize("obstruction", ["file", "escaped_symlink"])
def test_executor_default_file_refuses_without_provider_construction(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path,
    obstruction: str,
) -> None:
    async def run() -> None:
        registry, _, native, start = native_registry
        registry.paths.root.mkdir(parents=True)
        retained = registry.paths.workspace
        if obstruction == "escaped_symlink":
            outside = tmp_path / "outside"
            outside.mkdir()
            retained = outside / "retained.txt"
            registry.paths.workspace.symlink_to(outside, target_is_directory=True)
        retained.write_text("retain")
        assert isinstance(start.arguments, StartRun)
        resolved = replace(
            start.arguments.resolved_start,
            workspace_folder=Path("/"),
            workspace_path_policy=WorkspacePathPolicy.executor_default,
        )
        # Exercise wire hydration, not only the in-memory enum.
        args = decode_command_arguments(
            ChildOperation.start,
            {
                "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                "resolved_start": encode_value(resolved),
            },
        )
        outcome = await registry.run(replace(start, arguments=args))
        assert outcome.disposition is CommandDisposition.refused
        error = outcome.result["error_message"]
        assert isinstance(error, str)
        assert "Mac workspace unavailable" in error
        assert native.starts == 0
        assert retained.read_text() == "retain"

    asyncio.run(run())


def test_native_prompt_outcome_carries_positive_provider_acceptance(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run():
        registry, _spool, native, start = native_registry
        await registry.run(start)

        async def accepted(*args, **kwargs):
            return BackendPromptAccepted(True, agent_acceptance_proven=True)

        monkeypatch.setattr(native, "write_prompt", accepted)
        command = ChildCommand(
            "accepted-prompt",
            start.child,
            ChildOperation.write_prompt,
            TurnToken("c_one", 1),
            decode_command_arguments(
                ChildOperation.write_prompt,
                {
                    "content": encode_value(text_message_content("once")),
                    "sender_content": encode_value(text_message_content("once")),
                    "sender_label": "loop",
                    "mode": "queue",
                },
            ),
        )
        outcome = await registry.run(command)
        assert outcome.disposition is CommandDisposition.succeeded
        assert outcome.result["agent_acceptance_proven"] is True
        assert await registry.run(command) == outcome

    asyncio.run(run())
