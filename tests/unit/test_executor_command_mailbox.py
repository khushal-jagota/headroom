"""The command mailbox: every command is a row, judged by its own IDs.

Risk: a command lost or run twice when a request drops at a wait (poll reply, outcome
report, lease reply), a command landing for a replaced Mac process, a steer waiting on an
absent Mac, or a stop treated as done without proof. Gap: the WebSocket tests proved
none of this (commands lived in memory). Layer: unit, the real VpsExecutor over a real
SQLite database, without HTTP (the route adapters are covered in the integration test)
and without the conversation core (a fake records what the runs ask of it).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from tests.support.executor_presence import lease_request, mac_identity

import planner.executor.vps_mailbox as vps_mailbox
import planner.executor.vps_runs as vps_runs
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerRefused,
    BackendSteerUncertain,
    PromptWriteRefused,
    RemoteCommandUncertain,
    TurnToken,
)
from planner.conversation.contracts import (
    AnswerFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.lifecycle_storage import apply_facts
from planner.conversation.logic.retained_turn import UndeliveredTurnPrompt
from planner.conversation.message_content import text_message_content
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.sse import close_open_change_streams
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReport,
    CommandResult,
    EnvelopeKind,
    MailboxPollReply,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
)
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor
from planner.stalls.contracts import TrackedWaitKind
from planner.stalls.registry import WAITS

TURN = TurnToken("c", 1)
SETTLES = 5.0


@dataclass
class _Core:
    """The conversation core's side of MacRunConversations, recorded."""

    turn_live: bool = True
    retire_failures: int = 0
    retired: list[tuple[ChildReference, str]] = field(default_factory=list)
    undelivered: list[object] = field(default_factory=list)
    resumed: list[tuple[ChildReference, object, bool]] = field(default_factory=list)
    confirmed: list[ChildReference] = field(default_factory=list)
    stopped: list[ChildReference] = field(default_factory=list)

    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
    ) -> None:
        if self.turn_live:
            await emit()

    async def resume_undelivered_prompt(
        self, reference: ChildReference, prompt: object, *, start_needed: bool
    ) -> None:
        self.resumed.append((reference, prompt, start_needed))

    async def retire_replaced_remote_child(
        self,
        reference: ChildReference,
        *,
        replaced_by: str,
        undelivered_prompt: object = None,
    ) -> None:
        if self.retire_failures:
            self.retire_failures -= 1
            raise RuntimeError("the core could not retire it yet")
        self.retired.append((reference, replaced_by))
        self.undelivered.append(undelivered_prompt)

    async def confirm_remote_child_termination(self, reference: ChildReference) -> None:
        self.confirmed.append(reference)

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None:
        self.stopped.append(reference)


@dataclass
class _World:
    endpoint: VpsExecutor
    identity: ExecutorIdentity
    child: ChildReference
    proxy: MacRemoteChild
    core: _Core

    async def poll(
        self, after: int = 0, identity: ExecutorIdentity | None = None
    ) -> MailboxPollReply:
        return await self.endpoint.poll(identity or self.identity, after, 0)

    def rows(self) -> list[tuple[str, str]]:
        connection = connect(self.endpoint.db_path)
        try:
            return [
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT operation,state FROM executor_commands ORDER BY command_sequence"
                )
            ]
        finally:
            connection.close()

    def run_state(self) -> tuple[str, int]:
        connection = connect(self.endpoint.db_path)
        try:
            row = connection.execute(
                "SELECT stop_state,delivery_uncertain FROM mac_runs WHERE conversation_id='c'"
            ).fetchone()
            return row[0], row[1]
        finally:
            connection.close()

    async def report(self, *results: CommandResult) -> Any:
        return await self.endpoint.record_outcomes(self.identity, CommandOutcomeReport(results))


def _succeeded(command_id: str, child: ChildReference, **result: Any) -> CommandResult:
    return CommandResult(
        command_id,
        child_outcome=CommandOutcome(command_id, child, CommandDisposition.succeeded, result),
    )


@asynccontextmanager
async def _world(tmp_path: Path) -> AsyncIterator[_World]:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    create_schema(connection)
    connection.close()
    endpoint = VpsExecutor(path, RealClock())
    core = _Core()
    endpoint.runs.conversations = core
    # Model lists are their own case below; here they would only add rows.
    endpoint.presence.returned = None
    identity = mac_identity()
    await endpoint.lease(lease_request(identity))
    child = ChildReference(identity.executor_incarnation, "child", "c")
    endpoint.runs.store.register_child(child, "ticket", "ticket-1", 1)
    proxy = endpoint.runs.recover(child)
    assert proxy is not None
    try:
        yield _World(endpoint, identity, child, proxy, core)
    finally:
        await endpoint.close()


def _running_turn(db_path: str) -> None:
    """The lifecycle fact a steer's fetch-time expiry reads: turn 1 is running."""
    connection = connect(db_path)
    try:
        connection.execute(
            "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
            "access,created_at) VALUES ('c','codex','scripted','/tmp','full',1)"
        )
        apply_facts(
            connection,
            "c",
            (
                LifecycleFact(
                    LifecycleKind.turn, "1", 1, LifecycleOperation.turn, LifecycleStatus.running
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


async def _prompt(proxy: MacRemoteChild, turn: TurnToken = TURN) -> None:
    content = text_message_content("work")
    await proxy.write_prompt(
        turn,
        content,
        sender_label="owner",
        sender_content=content,
        mode=PromptDeliveryMode.queue,
        model_change=None,
        reasoning_effort_change=None,
    )


def test_a_command_waits_until_fetched_and_a_lost_poll_reply_is_answered_again(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await _prompt(world.proxy)  # accepted at commit, with nobody fetching
            assert world.rows() == [("write_prompt", "waiting")]
            assert world.run_state() == ("open", 1)

            first = await world.poll()
            assert [command.operation.value for command in first.commands] == ["write_prompt"]
            assert world.rows() == [("write_prompt", "delivered")]
            # The reply was lost: the Mac asks again from the same cursor.
            again = await world.poll()
            assert again == first
            sequence = first.commands[0].command_sequence
            assert (await world.poll(after=sequence)).commands == ()
            assert world.endpoint.mailbox.high_sequence() == sequence

            command_id = first.commands[0].command_id
            reply = await world.report(_succeeded(command_id, world.child, kind="prompt"))
            assert reply.recorded == (command_id,) and reply.rejected == ()
            assert world.rows() == [("write_prompt", "finished")]
            assert world.run_state() == ("open", 0)
            # The outcome reply was lost: the resend is recorded, and the first write wins.
            refused = CommandResult(
                command_id,
                child_outcome=CommandOutcome(
                    command_id, world.child, CommandDisposition.refused, {"error_type": "x"}
                ),
            )
            assert (await world.report(refused)).recorded == (command_id,)
            row = await asyncio.to_thread(world.endpoint.mailbox.command_row, command_id)
            assert row is not None and row.result is not None
            assert row.result.child_outcome is not None
            assert row.result.child_outcome.disposition is CommandDisposition.succeeded

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_held_poll_answers_as_soon_as_a_command_is_put(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            loop = asyncio.get_running_loop()
            started = loop.time()
            held = asyncio.create_task(world.endpoint.poll(world.identity, 0, 30))
            await asyncio.sleep(0.2)
            assert not held.done()
            await world.proxy.cancel_running_turn()
            reply = await asyncio.wait_for(held, SETTLES)
            assert [command.operation.value for command in reply.commands] == [
                "cancel_running_turn"
            ]
            assert loop.time() - started < SETTLES
            # Nothing to fetch: the hold ends at its wait, answering nothing.
            empty = await world.endpoint.poll(
                world.identity, reply.commands[0].command_sequence, 0.1
            )
            assert empty.commands == ()
            # A shutdown signal answers a held poll at once, so it cannot hold the
            # server's shutdown for the rest of its wait.
            held = asyncio.create_task(
                world.endpoint.poll(world.identity, reply.commands[0].command_sequence, 30)
            )
            await asyncio.sleep(0.2)
            await asyncio.to_thread(close_open_change_streams)
            assert (await asyncio.wait_for(held, SETTLES)).commands == ()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_results_that_can_never_be_recorded_are_rejected_one_by_one(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await _prompt(world.proxy)
            await world.proxy.cancel_running_turn()
            prompt, cancel = (await world.poll()).commands
            reply = await world.report(
                CommandResult("never-put", child_outcome=None),
                CommandResult(cancel.command_id),  # no outcome for a child command
                _succeeded(prompt.command_id, ChildReference("x", "y", "c")),  # another child
                _succeeded(cancel.command_id, world.child),
            )
            assert reply.recorded == (cancel.command_id,)
            assert {(item.command_id, item.reason) for item in reply.rejected} == {
                ("never-put", "unknown_command"),
                (cancel.command_id, "malformed_result"),
                (prompt.command_id, "malformed_result"),
            }

            # A new process: the old one's results are refused, and the row is settled.
            replacement = mac_identity()
            await world.endpoint.lease(lease_request(replacement))
            stale = await world.endpoint.record_outcomes(
                replacement, CommandOutcomeReport((_succeeded(prompt.command_id, world.child),))
            )
            assert stale.recorded == ()
            assert stale.rejected[0].reason == "command_of_another_executor_process"

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_new_mac_process_settles_the_old_ones_commands_by_its_own_cursor(
    tmp_path: Path,
) -> None:
    """Risk: a row marked delivered into a reply the old process never read is settled
    uncertain ("it may have run"), so a message that never ran is lost as interrupted.
    The successor journals into the same spool, so its cursor proves which rows the old
    process never journaled: those are withdrawn, and the rest settle uncertain."""

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await _prompt(world.proxy)
            journaled = (await world.poll()).commands[0]  # the old process journaled it
            await _prompt(world.proxy, TurnToken("c", 2))
            lost = (await world.poll(after=journaled.command_sequence)).commands[0]
            # Still waiting.
            await world.proxy.answer_permission_ask(
                "ask", "allow", answer_id="answer-1", turn_token=TurnToken("c", 2)
            )
            replacement = mac_identity()
            await world.endpoint.lease(lease_request(replacement))
            # The lease reply was lost: the same lease again changes nothing.
            await world.endpoint.lease(lease_request(replacement))
            # Waiting never ran: withdrawn at once. Delivered waits for the successor.
            assert world.rows() == [
                ("write_prompt", "delivered"),
                ("write_prompt", "delivered"),
                ("answer_permission_ask", "withdrawn"),
            ]
            # The successor's cursor is the old process's last journaled row.
            assert (
                await world.poll(after=journaled.command_sequence, identity=replacement)
            ).commands == ()
            assert world.rows() == [
                ("write_prompt", "finished"),
                ("write_prompt", "withdrawn"),
                ("answer_permission_ask", "withdrawn"),
            ]
            # The drain hands the core the turn's prompt, which provably never ran.
            for _ in range(int(SETTLES / 0.01)):
                if world.core.retired:
                    break
                await asyncio.sleep(0.01)
            (prompt,) = world.core.undelivered
            assert isinstance(prompt, UndeliveredTurnPrompt) and prompt.turn_number == 2
            assert prompt.content == text_message_content("work")
            for command, disposition in (
                (journaled, CommandDisposition.uncertain),
                (lost, CommandDisposition.refused),
            ):
                row = await asyncio.to_thread(
                    world.endpoint.mailbox.command_row, command.command_id
                )
                assert row is not None and row.result is not None
                assert row.result.child_outcome is not None
                assert row.result.child_outcome.disposition is disposition
            # The replaced process is refused, and must exit.
            refused = await world.endpoint.lease(lease_request(world.identity))
            assert not hasattr(refused, "accepted_at_vps_ms")
            # A command for the old child is refused at put, and nothing is inserted.
            with pytest.raises(PromptWriteRefused):
                await _prompt(world.proxy, TurnToken("c", 3))
            assert len(world.rows()) == 3

    asyncio.run(asyncio.wait_for(exercise(), 20))


@pytest.mark.parametrize("written", [False, True])
def test_a_startup_finishes_writing_again_a_prompt_a_crash_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: bool
) -> None:
    """Risk: the VPS stops while a never-ran prompt is being written to a fresh child.
    Cut before the fresh prompt row commits, the adopted turn never gets it; cut after,
    its interruption stays open and its recovery would send the message a second time.
    Gap: the core tests call the startup step directly; this proves the rows route to it."""
    import planner.conversation.deploy_interruptions.coordinator as coordinator

    closed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        coordinator,
        "close_mac_interruption_of_turn",
        lambda db, conversation, turn, now, outcome: closed.append((conversation, turn)),
    )

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await _prompt(world.proxy)
            await world.poll()  # Into the old process's dead socket.
            replacement = mac_identity()
            await world.endpoint.lease(lease_request(replacement))
            await world.poll(identity=replacement)  # Proves it never ran: withdrawn.
            for _ in range(int(SETTLES / 0.01)):
                if world.core.retired and world.run_state()[0] == "closed":
                    break
                await asyncio.sleep(0.01)
            # The rewrite's fresh child was registered at turn 1 before the crash.
            fresh = ChildReference(replacement.executor_incarnation, "fresh", "c")
            world.endpoint.runs.store.register_child(fresh, "ticket", "ticket-1", 2)
            connection = connect(world.endpoint.db_path)
            try:
                connection.execute("UPDATE mac_runs SET turn_number=1 WHERE conversation_id='c'")
                connection.commit()
            finally:
                connection.close()
            if written:
                proxy = world.endpoint.runs.recover(fresh)
                assert proxy is not None
                await _prompt(proxy)
            await world.endpoint.runs.resume_replacement_drains()
            for _ in range(int(SETTLES / 0.01)):
                if closed or world.core.resumed:
                    break
                await asyncio.sleep(0.01)
            if written:
                assert closed == [("c", 1)] and world.core.resumed == []
            else:
                ((child, prompt, start_needed),) = world.core.resumed
                assert child == fresh and start_needed and closed == []
                assert isinstance(prompt, UndeliveredTurnPrompt) and prompt.turn_number == 1

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_put_and_child_assignment_check_the_process_inside_their_own_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fence is read in the transaction that writes, so a process replaced between
    the caller's look and the write is never given a row or a child."""

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            entered, release = threading.Event(), threading.Event()
            original = vps_mailbox.SqliteCommandMailbox._insert_child_command

            def gated(self: Any, *args: Any) -> Any:
                entered.set()
                assert release.wait(SETTLES)
                return original(self, *args)

            monkeypatch.setattr(vps_mailbox.SqliteCommandMailbox, "_insert_child_command", gated)
            put = asyncio.create_task(world.proxy.cancel_running_turn())
            assert await asyncio.to_thread(entered.wait, SETTLES)
            replacement = mac_identity()
            await world.endpoint.lease(lease_request(replacement))
            release.set()
            await asyncio.wait_for(put, SETTLES)
            assert world.rows() == []

            # A child assigned under the process the caller last saw, now replaced.
            late = ChildReference(world.identity.executor_incarnation, "late", "d")
            with pytest.raises(BackendSpawnFailed):
                await asyncio.to_thread(
                    world.endpoint.runs.store.register_child, late, "ticket", "t", 1
                )
            connection = connect(world.endpoint.db_path)
            try:
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM executor_child_assignments WHERE child_id='late'"
                    ).fetchone()[0]
                    == 0
                )
            finally:
                connection.close()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_steer_waits_only_for_a_present_mac_and_only_for_its_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vps_runs, "STEER_DELIVERY_LIMIT_SECONDS", 0.3)
    not_delivered = BackendSteerRefused(PromptDeliveryRefusalReason.remote_steer_not_delivered)

    async def steer(world: _World) -> Any:
        return await world.proxy.steer(TURN, text_message_content("also"), sender_label="owner")

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            _running_turn(world.endpoint.db_path)
            # Not fetched within the limit: withdrawn, so the core sends it next.
            assert await steer(world) == not_delivered
            assert world.rows() == [("steer", "withdrawn")]

            # Fetched and answered.
            pending = asyncio.create_task(steer(world))
            reply = await world.endpoint.poll(world.identity, 1, SETTLES)
            command = reply.commands[0]
            await world.report(
                _succeeded(
                    command.command_id,
                    world.child,
                    kind="steer_accepted",
                    composed_content_delivered=True,
                )
            )
            assert await pending == BackendSteerAccepted(True)

            # Fetched, but no result by the limit: it may have reached the turn. Its row
            # says later whether it never did: the agent refused it, its turn had ended.
            pending = asyncio.create_task(steer(world))
            late = (
                await world.endpoint.poll(world.identity, command.command_sequence, SETTLES)
            ).commands[0]
            uncertain = await pending
            assert uncertain == BackendSteerUncertain(None)
            assert uncertain.not_delivered is not None
            await world.report(
                _succeeded(
                    late.command_id,
                    world.child,
                    kind="steer_refused",
                    refusal_reason=PromptDeliveryRefusalReason.no_running_turn_to_steer_into.value,
                )
            )
            assert await asyncio.wait_for(uncertain.not_delivered, SETTLES) is True

            # Away: no row is written at all.
            world.endpoint.presence._record = None
            before = len(world.rows())
            assert await steer(world) == not_delivered
            assert len(world.rows()) == before

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_held_poll_without_evidence_of_the_mac_marks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: a held poll outlives a Mac that went silent, and a put wakes it: the row is
    marked delivered into a reply nobody reads, so a steer can no longer be withdrawn
    and comes back uncertain instead of becoming the next message."""
    monkeypatch.setattr(vps_mailbox, "HELD_POLL_EVIDENCE_MS", 300)
    monkeypatch.setattr(vps_runs, "STEER_DELIVERY_LIMIT_SECONDS", 0.3)

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            _running_turn(world.endpoint.db_path)
            held = asyncio.create_task(world.endpoint.poll(world.identity, 0, 30))
            await asyncio.sleep(0.5)  # No lease since: the Mac may have gone silent.
            steered = await world.proxy.steer(
                TURN, text_message_content("also"), sender_label="owner"
            )
            assert steered == BackendSteerRefused(
                PromptDeliveryRefusalReason.remote_steer_not_delivered
            )
            assert (await asyncio.wait_for(held, SETTLES)).commands == ()
            assert world.rows() == [("steer", "withdrawn")]
            # A poll that has just arrived is its own evidence, and a fresh lease is too.
            await world.proxy.cancel_running_turn()
            assert len((await world.poll(after=1)).commands) == 1

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_put_whose_caller_is_cancelled_still_wakes_the_held_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: a cancelled caller (a steer's, a cancel's) commits its row but skips the
    wake-up, so the command waits out the poll's hold, and the high sequence lags."""

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            entered, release = threading.Event(), threading.Event()
            original = vps_mailbox.SqliteCommandMailbox._insert_child_command

            def gated(self: Any, *args: Any) -> Any:
                entered.set()
                assert release.wait(SETTLES)
                return original(self, *args)

            monkeypatch.setattr(vps_mailbox.SqliteCommandMailbox, "_insert_child_command", gated)
            held = asyncio.create_task(world.endpoint.poll(world.identity, 0, 30))
            put = asyncio.create_task(world.proxy.cancel_running_turn())
            assert await asyncio.to_thread(entered.wait, SETTLES)
            put.cancel()
            await asyncio.sleep(0)
            release.set()
            reply = await asyncio.wait_for(held, SETTLES)
            assert [command.operation.value for command in reply.commands] == [
                "cancel_running_turn"
            ]
            assert world.endpoint.mailbox.high_sequence() == reply.commands[0].command_sequence

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_stop_is_proven_only_by_its_child_stopped_item(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            stopping = asyncio.create_task(world.proxy.stop())
            command = (await world.endpoint.poll(world.identity, 0, SETTLES)).commands[0]
            assert world.run_state()[0] == "stopping"
            # A successful result alone proves nothing: the agent may still be running.
            await world.report(_succeeded(command.command_id, world.child))
            await asyncio.sleep(0.1)
            assert not stopping.done()
            assert world.rows() == [("stop", "delivered")]
            outcome = CommandOutcome(
                command.command_id, world.child, CommandDisposition.succeeded, {}
            )
            receipt = await world.endpoint.inbox.accept(
                QueuedEnvelope(
                    "mac", "c", 1, "stop-1", EnvelopeKind.child_stopped, as_object(outcome)
                ),
                world.identity,
            )
            assert receipt.disposition is ReceiptDisposition.accepted
            await asyncio.wait_for(stopping, SETTLES)
            assert world.rows() == [("stop", "finished")]
            assert world.run_state()[0] == "closed"
            # Its caller retires the child; the core is not asked a second time.
            assert world.core.confirmed == []

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_stop_the_mac_could_not_carry_out_fails_its_caller(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            stopping = asyncio.create_task(world.proxy.stop())
            command = (await world.endpoint.poll(world.identity, 0, SETTLES)).commands[0]
            await world.report(
                CommandResult(
                    command.command_id,
                    child_outcome=CommandOutcome(
                        command.command_id, world.child, CommandDisposition.uncertain, {}
                    ),
                )
            )
            with pytest.raises(RemoteCommandUncertain):
                await asyncio.wait_for(stopping, SETTLES)
            assert world.run_state()[0] == "stopping"
            # Its row is finished, but nothing proves the agent stopped: still a stall.
            connection = connect(world.endpoint.db_path)
            try:
                waits = world.endpoint.mailbox.stall_source().tracked_waits(connection, 0)
            finally:
                connection.close()
            assert [(wait.kind, wait.conversation_id) for wait in waits] == [
                (TrackedWaitKind.mac_stop_unproven, "c")
            ]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_stop_of_a_replaced_process_succeeds_once_its_items_have_drained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vps_runs, "DRAIN_RETRY_INITIAL_SECONDS", 0.05)

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            # The core's retirement fails once; the drain retries it, tracked meanwhile.
            world.core.retire_failures = 1
            stopping = asyncio.create_task(world.proxy.stop())
            stop = (await world.endpoint.poll(world.identity, 0, SETTLES)).commands[0]
            replacement = mac_identity()
            from planner.executor.protocol import WaitingConversation

            # The new process still holds the old one's items for this conversation.
            await world.endpoint.lease(
                lease_request(replacement, waiting=(WaitingConversation("c", 7),))
            )
            await asyncio.sleep(0.1)
            assert not stopping.done()
            assert world.run_state()[0] == "stopping"
            assert world.core.retired == []
            await world.endpoint.lease(lease_request(replacement))
            await asyncio.sleep(0.1)
            assert not stopping.done()  # The delivered stop waits for the successor's poll.
            await world.poll(after=stop.command_sequence, identity=replacement)
            await asyncio.wait_for(stopping, SETTLES)
            assert world.run_state()[0] == "closed"
            for _ in range(100):
                if world.core.retired:
                    break
                await asyncio.sleep(0.01)
            assert world.core.retired == [(world.child, replacement.executor_incarnation)]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_an_answer_is_saved_only_while_the_turn_it_answers_is_live(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            assert (
                await world.proxy.answer_permission_ask(
                    "ask", "allow", answer_id="answer-1", turn_token=TURN
                )
                is AnswerFate.saved
            )
            assert world.rows() == [("answer_permission_ask", "waiting")]
            world.core.turn_live = False
            assert (
                await world.proxy.answer_permission_ask(
                    "ask-2", "allow", answer_id="answer-2", turn_token=TURN
                )
                is AnswerFate.refused
            )
            assert world.rows() == [("answer_permission_ask", "waiting")]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_cancel_the_mac_could_not_carry_out_retires_the_child_in_an_owned_task(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await world.proxy.cancel_running_turn()  # returns at commit
            command = (await world.poll()).commands[0]
            reply = await world.report(
                CommandResult(
                    command.command_id,
                    child_outcome=CommandOutcome(
                        command.command_id, world.child, CommandDisposition.refused, {}
                    ),
                )
            )
            assert reply.recorded == (command.command_id,)
            # The child may still be working on a turn the VPS ended: it is stopped through
            # the mailbox, tracked while that runs, and retired on the stop's proof.
            stop = (
                await world.endpoint.poll(world.identity, command.command_sequence, SETTLES)
            ).commands[0]
            assert stop.operation.value == "stop"
            assert any(
                wait.kind is TrackedWaitKind.mac_cancel_retirement for wait in WAITS.snapshot()
            )
            outcome = CommandOutcome(stop.command_id, world.child, CommandDisposition.succeeded, {})
            await world.endpoint.inbox.accept(
                QueuedEnvelope(
                    "mac", "c", 1, "stop-1", EnvelopeKind.child_stopped, as_object(outcome)
                ),
                world.identity,
            )
            for _ in range(200):
                if world.core.confirmed:
                    break
                await asyncio.sleep(0.01)
            assert world.core.confirmed == [world.child]
            assert world.run_state()[0] == "closed"
            assert all(
                wait.kind is not TrackedWaitKind.mac_cancel_retirement for wait in WAITS.snapshot()
            )

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_withdrawn_initial_prompt_proves_it_never_ran(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await _prompt(world.proxy)
            world.proxy.initial_history_complete = True
            assert await world.proxy.withdraw_delivery() is True
            assert world.rows() == [("write_prompt", "withdrawn")]
            assert (await world.poll()).commands == ()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_vps_restart_keeps_the_mailbox_and_its_unproven_stops(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            await _prompt(world.proxy)
            stopping = asyncio.create_task(world.proxy.stop())
            await asyncio.sleep(0.1)
            stopping.cancel()
            restarted = VpsExecutor(world.endpoint.db_path, RealClock())
            try:
                identity = restarted.require_current(
                    incarnation_header=world.identity.executor_incarnation, protocol_header="22"
                )
                reply = await restarted.poll(identity, 0, 0)
                assert [command.operation.value for command in reply.commands] == [
                    "write_prompt",
                    "stop",
                ]
                connection = connect(restarted.db_path)
                try:
                    waits = restarted.mailbox.stall_source().tracked_waits(connection, 0)
                finally:
                    connection.close()
                assert {wait.kind for wait in waits} == {TrackedWaitKind.mac_stop_unproven}
                run = restarted.runs.retained_runs()[0]
                assert run.stop_requested and run.incarnation_current
            finally:
                await restarted.close()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_claude_model_change_asks_for_a_rebind_even_after_a_vps_restart(
    tmp_path: Path,
) -> None:
    """Risk: a Claude child takes its model and effort when it starts, and a prompt is
    accepted at commit, so a change sent through the mailbox comes back only as a failed
    turn instead of a rebind. After a VPS restart every Mac run is adopted through a new
    proxy, which must still know what its child started with. Layer: the real
    VpsExecutor and mailbox over SQLite; the proxy after restart comes from recover()."""
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.conversation.backends.contracts import NeedsRebind
    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationBackendKey,
        ConversationRoleMaterials,
        ResolvedConversationStart,
    )

    def start(backend: ConversationBackendKey) -> ResolvedConversationStart:
        return ResolvedConversationStart(
            "c",
            backend,
            "claude-old",
            "low",
            ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "ticket-1"),)),
            tmp_path,
            ConversationAccess.full,
        )

    async def prompt(proxy: MacRemoteChild, model: str | None, effort: str | None) -> None:
        content = text_message_content("work")
        await proxy.write_prompt(
            TURN,
            content,
            sender_label="owner",
            sender_content=content,
            mode=PromptDeliveryMode.queue,
            model_change=model,
            reasoning_effort_change=effort,
            reasoning_effort_is_set=effort is not None,
        )

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            started = MacRemoteChild(
                world.child, world.endpoint.runs, lambda resolved: canonical_snapshot()
            )
            await started.start(start(ConversationBackendKey.claude), vendor_session_cursor=None)
            # After a VPS restart the run is bound again through a new proxy.
            adopted = world.endpoint.runs.recover(world.child)
            assert adopted is not None and adopted is not started
            for proxy in (started, adopted):
                for model, effort in (("claude-new", None), (None, "high")):
                    with pytest.raises(NeedsRebind):
                        await prompt(proxy, model, effort)
            assert [operation for operation, _ in world.rows()] == ["start"]
            # The same model and effort are no change: the prompt is put.
            await prompt(adopted, "claude-old", "low")
            assert [operation for operation, _ in world.rows()] == ["start", "write_prompt"]

    asyncio.run(asyncio.wait_for(exercise(), 20))
