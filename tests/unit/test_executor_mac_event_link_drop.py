"""A dropped Mac request must never freeze a Mac event, wherever the event is waiting.

On 7 November 2026 a Mac backend event passed its first connection check, waited for
the conversation lock, and then failed its write because the link had dropped in
between. The store parked the write on an event nothing ever set, holding the
conversation lock and the inbox lock forever. The link is now HTTPS mailboxes: an item
arrives in a route-4 batch (VpsExecutor.accept_items), and the Mac is present while its
leases arrive. These tests drop the request (the client goes away, so the route call is
cancelled) and let the lease lapse, and also lease again from the same process, at each
place a Mac event can wait on its way into the record. They hold some of those waits past
the time limit, and they let another process lease while the event waits.

Rules proved here:
- An event is judged by its sender's process (executor_incarnation), never by the
  request or by presence: a cancelled route call does not cancel its save, because the
  batch runs in a task VpsExecutor owns and awaits shielded.
- The sender is fenced inside the receipt's transaction: once another process is
  current, the waiting event of the old one gets no receipt and saves nothing, and the
  new process's resend of it saves exactly once while the child's assignment is open.
- A wait past MAC_ITEM_RETRY_LATER_SECONDS, or a failed write, answers "retry later":
  no receipt, nothing recorded, memory as it was, every lock let go, no task left behind.
  Route 4 says it as ItemBatchReply.stop, never as an error status.
- The resend gets one receipt and the event takes effect exactly once.

Layer: the real conversation core, inbox and SQLite (the _case harness of
test_executor_event_persistence), with VpsExecutor's typed route adapters in place of
HTTP. Contract §8.4, as amended by §11.2 M7.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.support.executor_presence import lease_request, mac_identity
from tests.unit.test_conversation_api import _Harness
from tests.unit.test_conversation_executor_publication import _envelope
from tests.unit.test_executor_event_persistence import _case

import planner.executor.vps_inbox as vps_inbox
from planner.conversation.backends.contracts import BackendChild, ToolCallStarted, TurnToken
from planner.conversation.events import ConversationEventKind
from planner.conversation.system import (
    SqliteProcessConversationSystem,
    _ConversationState,
    _ReservedTurn,
)
from planner.core.clock import RealClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import ChangeSignallingConnection
from planner.execution_configuration import PROTOCOL_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.configuration import MAC_AWAY_AFTER_SECONDS
from planner.executor.protocol import (
    AcceptanceReceipt,
    BackendEvent,
    BackendEventMethod,
    EnvelopeKind,
    ItemBatch,
    ItemBatchReply,
    ItemSubmission,
    JsonObject,
    LeaseReply,
    QueuedEnvelope,
    ReceiptDisposition,
    WaitingConversation,
)
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_transport import VpsExecutor

# Generous bounds for a test that must fail, not hang, if a wait is unbounded again.
SETTLES_WITHIN_SECONDS = 5.0
# Short enough to keep the suite fast; the production value is in core/config.py.
TEST_RETRY_LATER_SECONDS = 0.3


async def _until(condition: Callable[[], bool], what: str) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the event never reached {what}")


def _waiting_on(primitive: asyncio.Lock | asyncio.Event) -> bool:
    return bool(getattr(primitive, "_waiters", None))


def _batch(envelope: QueuedEnvelope) -> ItemBatch:
    return ItemBatch(envelope.sender_id, (ItemSubmission(envelope),))


@dataclass
class _Conversations:
    """The conversation core as MacRuns sees it. retire_replaced_remote_child is only
    recorded and closes nothing, as when the core no longer holds the child, so the drain
    closes the run itself; the rest is the real system's."""

    system: SqliteProcessConversationSystem
    retired: list[tuple[ChildReference, str]] = field(default_factory=list)

    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
    ) -> None:
        await self.system.emit_answer(turn, emit)

    async def confirm_remote_child_termination(self, reference: ChildReference) -> None:
        await self.system.confirm_remote_child_termination(reference)

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None:
        await self.system.stop_interrupted_remote_child(reference, child)

    async def resume_undelivered_prompt(
        self, reference: ChildReference, prompt: object, *, start_needed: bool
    ) -> None:
        raise AssertionError("no prompt is written again here")

    async def retire_replaced_remote_child(
        self,
        reference: ChildReference,
        *,
        replaced_by: str,
        undelivered_prompt: object = None,
    ) -> None:
        self.retired.append((reference, replaced_by))


@dataclass
class _World:
    harness: _Harness
    store: InboxStore
    identity: ExecutorIdentity
    event: BackendEvent
    envelope: QueuedEnvelope
    endpoint: VpsExecutor
    state: _ConversationState
    conversations: _Conversations

    @property
    def inbox_lock(self) -> asyncio.Lock:
        return self.endpoint.inbox._conversation_locks.setdefault("c", asyncio.Lock())

    def send(
        self, envelope: QueuedEnvelope | None = None, identity: ExecutorIdentity | None = None
    ) -> asyncio.Task[ItemBatchReply]:
        """The Mac's route-4 request for one item, as a task the test may cancel."""
        return asyncio.create_task(
            self.endpoint.accept_items(
                identity or self.identity, "c", _batch(envelope or self.envelope)
            )
        )

    def owned_work(self) -> asyncio.Task[Any]:
        """The batch's own task, which a cancelled route call leaves running."""
        (work,) = tuple(self.endpoint._owned)
        return work

    async def drop_request(self, route: asyncio.Task[ItemBatchReply], link_event: str) -> None:
        """The client goes away (its route call is cancelled), its leases stop until the
        Mac is judged away, and with "reconnected" the same process leases again."""
        route.cancel()
        with pytest.raises(asyncio.CancelledError):
            await route
        self.lapse_lease()
        if link_event == "reconnected":
            await self.lease()
        else:
            assert link_event == "lost"

    def lapse_lease(self) -> None:
        """No lease for longer than MAC_AWAY_AFTER_SECONDS, by the VPS clock."""
        skew_ms = int((MAC_AWAY_AFTER_SECONDS + 1) * 1000)
        self.endpoint.presence._clock = lambda: now_ms() + skew_ms
        assert self.endpoint.presence.judgement().presence == "not_heard_from"

    async def lease(
        self,
        identity: ExecutorIdentity | None = None,
        waiting: tuple[WaitingConversation, ...] = (),
    ) -> None:
        reply = await self.endpoint.lease(lease_request(identity or self.identity, waiting=waiting))
        assert isinstance(reply, LeaseReply)
        assert self.endpoint.presence.is_present()

    async def tool_rows(self) -> int:
        rows = await self.harness.store.read_events_after("c", 0)
        return sum(row.kind is ConversationEventKind.tool_call_started for row in rows)

    async def assert_nothing_held(self) -> None:
        assert not self.inbox_lock.locked()
        assert not self.state.lock.locked()
        await self.no_task_left_behind()

    async def no_task_left_behind(self) -> None:
        # A finished task leaves the set on the loop's next turn.
        await _until(lambda: not self.state.settlement_tasks, "no settlement task left")
        await _until(lambda: not self.endpoint._owned, "no owned route task left")


@asynccontextmanager
async def _world(root: Path) -> AsyncIterator[_World]:
    async with _case(root, True) as (harness, store, identity, event, envelope):
        state = harness.system._conversations["c"]
        await state.backend_event_queue.join()
        # _case made identity's process current, so the endpoint starts with it present.
        endpoint = VpsExecutor(str(harness.db_path), RealClock())
        endpoint.bind_conversations(harness.system)
        endpoint.presence.returned = None  # no model-list rows on a lease
        conversations = _Conversations(harness.system)
        endpoint.runs.conversations = conversations
        try:
            yield _World(harness, store, identity, event, envelope, endpoint, state, conversations)
        finally:
            await endpoint.close()


# --- where a Mac event can wait --------------------------------------------------------

# Armed before the event is sent: returns (until the event waits there, release it).
_Hold = tuple[Callable[[], Awaitable[None]], Callable[[], None]]
_HoldFactory = Callable[[_World], Awaitable[_Hold]]


def _thread_gate(
    monkeypatch: pytest.MonkeyPatch, target: object, name: str
) -> tuple[threading.Event, threading.Event]:
    """Stop a blocking step, in its worker thread, before it does its work."""
    entered, release = threading.Event(), threading.Event()
    original = getattr(target, name)

    def gated(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(SETTLES_WITHIN_SECONDS):
            raise AssertionError("the test never released this step")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, gated)
    return entered, release


def _holds(monkeypatch: pytest.MonkeyPatch) -> dict[str, _HoldFactory]:

    async def preflight(world: _World) -> _Hold:
        entered, release = _thread_gate(monkeypatch, world.endpoint.inbox.store, "preflight")

        async def reached() -> None:
            assert await asyncio.to_thread(entered.wait, SETTLES_WITHIN_SECONDS)

        return reached, release.set

    async def authority(world: _World) -> _Hold:
        entered, release = _thread_gate(monkeypatch, world.endpoint.inbox.runs, "authority")

        async def reached() -> None:
            assert await asyncio.to_thread(entered.wait, SETTLES_WITHIN_SECONDS)

        return reached, release.set

    async def reservation(world: _World) -> _Hold:
        running = world.state.running_turn
        assert running is not None
        reserved = _ReservedTurn(token=running.token, resolved=asyncio.Event())
        world.state.reserved_turn = reserved

        def release() -> None:
            world.state.reserved_turn = None
            reserved.resolved.set()

        async def reached() -> None:
            await _until(lambda: _waiting_on(reserved.resolved), "the turn reservation")

        return reached, release

    async def steer_barrier(world: _World) -> _Hold:
        barrier = asyncio.Event()
        world.state.steer_settlements.add(barrier)

        def release() -> None:
            world.state.steer_settlements.discard(barrier)
            barrier.set()

        async def reached() -> None:
            await _until(lambda: _waiting_on(barrier), "the steer barrier")

        return reached, release

    async def conversation_lock(world: _World) -> _Hold:
        # Any ordinary holder of the conversation lock: the 7 November timing.
        await world.state.lock.acquire()

        async def reached() -> None:
            await _until(lambda: _waiting_on(world.state.lock), "the conversation lock")

        def release() -> None:
            world.state.lock.release()

        return reached, release

    async def inbox_lock(world: _World) -> _Hold:
        lock = world.inbox_lock
        await lock.acquire()  # an earlier item of this conversation still being accepted

        async def reached() -> None:
            await _until(lambda: _waiting_on(lock), "the inbox lock")

        return reached, lock.release

    async def write(world: _World) -> _Hold:
        # Past every wait and holding the conversation lock, as its write begins: the
        # in-transaction receipt lookup and sender fence run after this.
        entered, release = asyncio.Event(), asyncio.Event()
        original = world.harness.store.commit_backend_event

        async def gated(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(world.harness.store, "commit_backend_event", gated)

        async def reached() -> None:
            await asyncio.wait_for(entered.wait(), SETTLES_WITHIN_SECONDS)

        return reached, release.set

    return {
        "preflight": preflight,
        "authority": authority,
        "reservation": reservation,
        "steer_barrier": steer_barrier,
        "conversation_lock": conversation_lock,
        "inbox_lock": inbox_lock,
        "write": write,
    }


ALL_WAITS = (
    "preflight",
    "authority",
    "reservation",
    "steer_barrier",
    "conversation_lock",
    "inbox_lock",
    "write",
)
# The waits with a time limit. Preflight, the authority check and the write are single
# SQLite transactions, bounded by the busy timeout; a write under way is never abandoned.
LIMITED_WAITS = ("reservation", "steer_barrier", "conversation_lock", "inbox_lock")


def _no_save_trouble(caplog: pytest.LogCaptureFixture) -> None:
    trouble = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == "planner.conversation"
    ]
    assert trouble == []


def _only_receipt(reply: ItemBatchReply) -> AcceptanceReceipt:
    assert reply.stop is None
    (receipt,) = reply.receipts
    return receipt


@pytest.mark.parametrize(
    ("wait_point", "link_event"),
    [(point, event) for point in ALL_WAITS for event in ("lost", "reconnected")]
    # The control: the same wait with the request and the lease left alone.
    + [("conversation_lock", "none")],
)
def test_a_link_drop_while_a_mac_event_waits_does_not_fail_its_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    wait_point: str,
    link_event: str,
) -> None:
    caplog.set_level(logging.WARNING, logger="planner.conversation")

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            reached, release = await _holds(monkeypatch)[wait_point](world)
            route = world.send()
            await reached()
            work = world.owned_work()
            # A read of the conversation answers while the event waits, even when the
            # wait is the event's own write holding the conversation lock.
            assert await asyncio.wait_for(world.harness.system.held_prompts("c"), 1) == ()
            if link_event != "none":
                await world.drop_request(route, link_event)
                # The client is gone; its batch is not.
                assert not work.done()
            release()

            reply = await asyncio.wait_for(
                route if link_event == "none" else work, SETTLES_WITHIN_SECONDS
            )
            receipt = _only_receipt(reply)
            assert receipt.disposition is ReceiptDisposition.accepted
            assert receipt.result == {"applied": True}
            assert await world.tool_rows() == 1
            await world.assert_nothing_held()

            # The Mac leases again (if it has not yet) and resends the same head item.
            if link_event == "lost":
                await world.lease()
            resend = await asyncio.wait_for(world.send(), SETTLES_WITHIN_SECONDS)
            assert _only_receipt(resend) == receipt
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                == receipt
            )
            assert await world.tool_rows() == 1
            await world.assert_nothing_held()

    asyncio.run(asyncio.wait_for(exercise(), 20))
    _no_save_trouble(caplog)


@pytest.mark.parametrize("wait_point", ALL_WAITS)
def test_a_new_mac_process_leasing_while_an_event_waits_saves_its_resend_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_point: str
) -> None:
    """Contract §11.2 M7 (overriding §8.4's "the event still saves"): the binding fence
    is in the event's write, so the old process's waiting event is refused without a
    receipt. The new process holds the same outbox item and sends it again; the child is
    of the old process, and its assignment stays open until the new process's lease
    shows the conversation drained, so the resend saves exactly once. Then the run of
    the replaced process is handed to the conversation core and closed."""

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            running = world.state.running_turn
            reached, release = await _holds(monkeypatch)[wait_point](world)
            route = world.send()
            await reached()
            # The Mac process restarts: a new incarnation leases, its outbox still
            # holding this conversation's item.
            successor = mac_identity()
            await world.lease(successor, (WaitingConversation("c", world.envelope.sequence),))
            release()

            with pytest.raises(PlannerError) as refused:
                await asyncio.wait_for(route, SETTLES_WITHIN_SECONDS)
            assert refused.value.code is ErrorCode.executor_incarnation_not_current
            # No receipt: the item is not dropped for its sender's successor.
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                is None
            )
            assert await world.tool_rows() == 0
            assert world.state.running_turn is running
            await world.assert_nothing_held()

            # The drain waits: the successor's lease lists the conversation as waiting.
            assert world.conversations.retired == []
            assert world.endpoint.runs.store.child_assignment(world.event.child) is not None

            resend = await asyncio.wait_for(world.send(identity=successor), SETTLES_WITHIN_SECONDS)
            receipt = _only_receipt(resend)
            assert receipt.disposition is ReceiptDisposition.accepted
            assert receipt.result == {"applied": True}
            again = await asyncio.wait_for(world.send(identity=successor), SETTLES_WITHIN_SECONDS)
            assert _only_receipt(again) == receipt
            assert await world.tool_rows() == 1
            await world.assert_nothing_held()

            # The successor's next lease shows nothing waiting: the old run goes to the
            # conversation core, once, and closes.
            await world.lease(successor)
            await _until(lambda: bool(world.conversations.retired), "the end of its drain")
            assert world.conversations.retired == [
                (world.event.child, successor.executor_incarnation)
            ]
            store = world.endpoint.runs.store
            await _until(lambda: store.has_closed_child_assignment(world.event.child), "a close")
            assert store.unresolved_runs_of_replaced_incarnations() == ()

    asyncio.run(asyncio.wait_for(exercise(), 20))


@pytest.mark.parametrize("wait_point", LIMITED_WAITS)
def test_a_mac_event_held_past_the_limit_is_told_to_retry_later_and_applies_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_point: str
) -> None:
    monkeypatch.setattr(vps_inbox, "MAC_ITEM_RETRY_LATER_SECONDS", TEST_RETRY_LATER_SECONDS)

    async def exercise() -> None:
        async with _world(tmp_path) as world:

            def told_to_retry_later(reply: ItemBatchReply) -> None:
                assert reply.receipts == ()
                assert reply.stop is not None and reply.stop.reason == "retry_later"
                assert reply.stop.sequence == world.envelope.sequence

            running = world.state.running_turn
            assert running is not None
            reached, release = await _holds(monkeypatch)[wait_point](world)
            route = world.send()
            await reached()
            world.lapse_lease()

            # Route 4 never answers an error for this: retry later is the reply's stop.
            told_to_retry_later(await asyncio.wait_for(route, SETTLES_WITHIN_SECONDS))
            # Retry later is not a refusal: no receipt, no position, nothing recorded.
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                is None
            )
            assert await world.tool_rows() == 0
            # Memory is as the event found it, and nothing still waits on its behalf.
            assert world.state.running_turn is running
            await world.no_task_left_behind()
            if wait_point != "inbox_lock":
                assert not world.inbox_lock.locked()
            if wait_point != "conversation_lock":
                assert not world.state.lock.locked()

            # The Mac leases again and resends while the wait still holds: the same
            # answer, and no task piles up. A single item accepted directly is told the
            # same as a PlannerError.
            await world.lease()
            told_to_retry_later(await asyncio.wait_for(world.send(), SETTLES_WITHIN_SECONDS))
            with pytest.raises(PlannerError) as refused:
                await asyncio.wait_for(
                    world.endpoint.inbox.accept(world.envelope, world.identity),
                    SETTLES_WITHIN_SECONDS,
                )
            assert refused.value.code is ErrorCode.retry_later
            await world.no_task_left_behind()
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                is None
            )

            release()
            receipt = _only_receipt(await asyncio.wait_for(world.send(), SETTLES_WITHIN_SECONDS))
            assert receipt.disposition is ReceiptDisposition.accepted
            assert receipt.result == {"applied": True}
            assert _only_receipt(await world.send()) == receipt
            assert await world.tool_rows() == 1
            await world.assert_nothing_held()

    asyncio.run(asyncio.wait_for(exercise(), 20))


async def _recorded_endings(world: _World) -> int:
    rows = await world.harness.store.read_events_after("c", 0)
    return sum(row.kind is ConversationEventKind.turn_ended for row in rows)


# The 26 October 2026 error: a programming fault that fails the same way every time.
DETERMINISTIC_SAVE_ERROR = "runtime candidates cannot enter nested savepoints"


@pytest.mark.parametrize("failing_step", ["mark_changed", "commit"])
@pytest.mark.parametrize("failure", ["transient", "deterministic"])
def test_a_failed_mac_write_restores_memory_and_is_retried_or_refused_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failing_step: str,
    failure: str,
) -> None:
    """A turn ending moves runtime fields before COMMIT so publication reads them.

    If the change-mark INSERT or COMMIT then fails, those fields must go back: the
    resend is interpreted from memory, and a turn memory already thinks is over would be
    recorded as stale news, leaving the record's turn running for good. A failure that
    can pass is answered retry later and the resend applies once. One that would fail
    every time is refused once, durably and with a log line, instead of looping.
    """
    caplog.set_level(logging.ERROR, logger="planner.executor.vps_inbox")
    failing = threading.local()

    def fail_here() -> None:
        if getattr(failing, "armed", False):
            failing.armed = False
            if failure == "transient":
                raise sqlite3.OperationalError("disk I/O error")
            raise RuntimeError(DETERMINISTIC_SAVE_ERROR)

    if failing_step == "commit":
        original_commit = ChangeSignallingConnection._sqlite_commit

        def commit(connection: ChangeSignallingConnection) -> None:
            fail_here()
            original_commit(connection)

        monkeypatch.setattr(ChangeSignallingConnection, "_sqlite_commit", commit)
    else:
        import planner.conversation.publication.persistence as persistence

        original_mark = persistence.mark_changed

        def mark_changed(*args: Any, **kwargs: Any) -> Any:
            fail_here()
            return original_mark(*args, **kwargs)

        monkeypatch.setattr(persistence, "mark_changed", mark_changed)

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            running = world.state.running_turn
            assert running is not None
            ended = BackendEvent(
                world.event.child,
                BackendEventMethod.turn_ended,
                running.token,
                {"ending": "completed"},
            )
            envelope = _envelope(2, EnvelopeKind.backend_event, ended)
            store = world.endpoint.inbox.store
            original_accept = store.accept_in_transaction

            armings: list[None] = []

            def accept_then_arm(*args: Any, **kwargs: Any) -> Any:
                result = original_accept(*args, **kwargs)
                if not armings:  # the event's own write; never the refusal's write
                    armings.append(None)
                    failing.armed = True  # the next step of this write thread fails once
                return result

            monkeypatch.setattr(store, "accept_in_transaction", accept_then_arm)
            last_ended = world.state.last_ended_turn_token

            if failure == "transient":
                with pytest.raises(PlannerError) as refused:
                    await asyncio.wait_for(
                        world.endpoint.inbox.accept(envelope, world.identity),
                        SETTLES_WITHIN_SECONDS,
                    )
                assert refused.value.code is ErrorCode.retry_later
                assert lookup_receipt(store, envelope.sender_id, envelope.submission_id) is None
            else:
                refusal = await asyncio.wait_for(
                    world.endpoint.inbox.accept(envelope, world.identity), SETTLES_WITHIN_SECONDS
                )
                assert refusal.disposition is ReceiptDisposition.refused
                error = cast(dict[str, Any], refusal.result)["error"]
                assert error["code"] == "save_failed"
                assert DETERMINISTIC_SAVE_ERROR in error["message"]
                assert lookup_receipt(store, envelope.sender_id, envelope.submission_id) == refusal
                assert any(
                    "conversation=c" in record.getMessage() and "sequence=2" in record.getMessage()
                    for record in caplog.records
                )
            # Memory is as the rolled-back rows are.
            assert world.state.running_turn is running and not running.ended
            assert world.state.last_ended_turn_token == last_ended
            assert await _recorded_endings(world) == 0
            await world.assert_nothing_held()

            monkeypatch.setattr(store, "accept_in_transaction", original_accept)
            await world.lease()
            receipt = await asyncio.wait_for(
                world.endpoint.inbox.accept(envelope, world.identity), SETTLES_WITHIN_SECONDS
            )
            if failure == "transient":
                assert receipt.disposition is ReceiptDisposition.accepted
                assert receipt.result == {"applied": True}
                assert world.state.running_turn is None
                assert world.state.last_ended_turn_token == running.token
                assert await _recorded_endings(world) == 1
            else:
                # The refusal stands: the resend gets it again and nothing is retried.
                assert receipt == refusal
                assert world.state.running_turn is running
                assert await _recorded_endings(world) == 0
            assert await world.endpoint.inbox.accept(envelope, world.identity) == receipt
            assert await _recorded_endings(world) == (1 if failure == "transient" else 0)
            await world.harness.system.wait_until_quiescent()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_an_agent_write_past_the_limit_finishes_once_and_its_resend_gets_its_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Mac agent's write (cli_write) can deliver into other conversations, so it is
    never stopped part-way. Its answer still comes by the limit; nothing of the same
    conversation runs until it ends, and the resend never runs it a second time."""
    monkeypatch.setattr(vps_inbox, "MAC_ITEM_RETRY_LATER_SECONDS", TEST_RETRY_LATER_SECONDS)

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            store = world.endpoint.inbox.store
            started: list[int] = []
            release = asyncio.Event()

            def delivered(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                return {"delivered": True}

            async def slow_delivery(
                envelope: QueuedEnvelope, identity: ExecutorIdentity
            ) -> AcceptanceReceipt:
                started.append(envelope.sequence)
                await release.wait()  # e.g. another conversation's lock, or its retries
                return await asyncio.to_thread(
                    store.accept,
                    envelope,
                    delivered,
                    1,
                    identity=identity,
                )

            monkeypatch.setattr(world.endpoint.inbox.agent_writes, "accept", slow_delivery)
            first = replace(world.envelope, sequence=1, kind=EnvelopeKind.cli_write)
            later = _envelope(2, EnvelopeKind.backend_event, world.event)

            for item in (first, first, later):
                with pytest.raises(PlannerError) as answered:
                    await asyncio.wait_for(
                        world.endpoint.inbox.accept(item, world.identity), SETTLES_WITHIN_SECONDS
                    )
                assert answered.value.code is ErrorCode.retry_later
            assert started == [1]  # never twice, and the later item never overtook it
            assert lookup_receipt(store, first.sender_id, first.submission_id) is None
            assert await world.tool_rows() == 0

            release.set()
            receipt = await asyncio.wait_for(
                world.endpoint.inbox.accept(first, world.identity), SETTLES_WITHIN_SECONDS
            )
            assert receipt.disposition is ReceiptDisposition.accepted
            assert receipt.result == {"delivered": True}
            assert started == [1]
            assert not world.endpoint.inbox._still_settling
            assert not world.inbox_lock.locked()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_an_event_from_a_replaced_mac_process_is_refused_before_its_write(
    tmp_path: Path,
) -> None:
    """Its requests are refused at the header fence, from memory. One that got past it
    just before the switch is refused by the binding fence, which runs inside the
    receipt's transaction (InboxStore.accept_in_transaction): no receipt, nothing
    recorded, memory as it was. Preflight and the authority check may run first; they
    write nothing, and the child's assignment stays open for the successor's replay, so
    the fence alone refuses here."""

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            running = world.state.running_turn
            # A new Mac process takes over, its outbox still holding this item.
            successor = mac_identity()
            await world.lease(successor, (WaitingConversation("c", world.envelope.sequence),))

            with pytest.raises(PlannerError) as fenced:
                world.endpoint.require_current(
                    incarnation_header=world.identity.executor_incarnation,
                    protocol_header=str(PROTOCOL_VERSION),
                )
            assert fenced.value.code is ErrorCode.executor_incarnation_not_current
            with pytest.raises(PlannerError) as refused:
                await asyncio.wait_for(world.send(), SETTLES_WITHIN_SECONDS)
            assert refused.value.code is ErrorCode.executor_incarnation_not_current
            # No refusal receipt either: the item is not dropped for its sender.
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                is None
            )
            assert await world.tool_rows() == 0
            assert world.state.running_turn is running
            await world.assert_nothing_held()

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_the_vps_local_path_with_the_same_timing_is_unaffected(tmp_path: Path) -> None:
    """A child on the VPS reports through the local queue, with no sender check at all."""

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            sink = world.harness.backends["c"].sink
            assert sink is not None and world.event.turn is not None
            await world.state.lock.acquire()
            await sink.report(
                world.event.turn,
                ToolCallStarted(tool_call_id="tool", title="Work", tool_kind="other"),
            )
            await _until(lambda: _waiting_on(world.state.lock), "the conversation lock")
            world.lapse_lease()
            world.state.lock.release()
            await asyncio.wait_for(world.state.backend_event_queue.join(), SETTLES_WITHIN_SECONDS)
            assert await world.tool_rows() == 1

    asyncio.run(asyncio.wait_for(exercise(), 20))
