"""A dropped Mac link must never freeze a Mac event, wherever the event is waiting.

On 7 November 2026 a Mac backend event passed its first connection check, waited for
the conversation lock, and then failed its write because the link had dropped in
between. The store parked the write on an event nothing ever set, holding the
conversation lock and the inbox lock forever. These tests drop the link (and also
reconnect with a newer connection_generation) at each place a Mac event can wait on its
way into the record, and hold some of those waits past the time limit.

Rules proved here:
- An event is judged by its sender's process (executor_incarnation), never by the link.
- A wait past MAC_ITEM_RETRY_LATER_SECONDS, or a failed write, answers "retry later":
  no receipt, nothing recorded, memory as it was, every lock let go, no task left behind.
- The resend gets one receipt and the event takes effect exactly once.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.unit.test_conversation_api import _Harness
from tests.unit.test_conversation_executor_publication import _envelope
from tests.unit.test_executor_event_persistence import _case

import planner.executor.vps_inbox as vps_inbox
from planner.conversation.backends.contracts import ToolCallStarted
from planner.conversation.events import ConversationEventKind
from planner.conversation.system import _ConversationState, _ReservedTurn
from planner.core.clock import RealClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import ChangeSignallingConnection
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.link import Link
from planner.executor.protocol import (
    AcceptanceReceipt,
    BackendEvent,
    BackendEventMethod,
    EnvelopeKind,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
    as_object,
)
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_transport import VpsExecutor

# Generous bounds for a test that must fail, not hang, if a wait is unbounded again.
SETTLES_WITHIN_SECONDS = 5.0
# Short enough to keep the suite fast; the production value is in core/config.py.
TEST_RETRY_LATER_SECONDS = 0.3


def _reconnected(identity: ExecutorIdentity) -> ExecutorIdentity:
    """The same Mac process on its next link: same incarnation, newer generation."""
    return ExecutorIdentity(
        PROTOCOL_VERSION,
        "a" * 40,
        SPOOL_FORMAT_VERSION,
        identity.executor_incarnation,
        identity.connection_generation + 1,
    )


async def _until(condition: Callable[[], bool], what: str) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the event never reached {what}")


def _waiting_on(primitive: asyncio.Lock | asyncio.Event) -> bool:
    return bool(getattr(primitive, "_waiters", None))


@dataclass
class _World:
    harness: _Harness
    store: InboxStore
    identity: ExecutorIdentity
    event: BackendEvent
    envelope: QueuedEnvelope
    endpoint: VpsExecutor
    state: _ConversationState

    @property
    def inbox_lock(self) -> asyncio.Lock:
        return self.endpoint.inbox._conversation_locks.setdefault("c", asyncio.Lock())

    async def drop_link(self, link_event: str) -> ExecutorIdentity:
        """What VpsExecutor.link_down writes, then optionally the Mac's reconnect."""
        await asyncio.to_thread(self.store.runs.mark_lost, self.identity)
        if link_event == "lost":
            return self.identity
        assert link_event == "reconnected"
        return await self.reconnect()

    async def reconnect(self) -> ExecutorIdentity:
        current = self.endpoint.runs.identity
        assert current is not None
        await asyncio.to_thread(self.store.runs.mark_lost, current)
        following = _reconnected(current)
        await asyncio.to_thread(self.store.runs.begin_connection, following)
        await asyncio.to_thread(self.store.runs.mark_ready, following)
        self.endpoint.runs.identity = following
        return following

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


@asynccontextmanager
async def _world(root: Path) -> AsyncIterator[_World]:
    async with _case(root, True) as (harness, store, identity, event, envelope):
        state = harness.system._conversations["c"]
        await state.backend_event_queue.join()
        endpoint = VpsExecutor(str(harness.db_path), RealClock())
        endpoint.bind_conversations(harness.system)
        endpoint.runs.identity = identity
        yield _World(harness, store, identity, event, envelope, endpoint, state)


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
        # in-transaction receipt lookup that refused on 7 November runs after this.
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


@pytest.mark.parametrize(
    ("wait_point", "link_event"),
    [(point, event) for point in ALL_WAITS for event in ("lost", "reconnected")]
    # The control: the same wait with the link left alone.
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
            accept = asyncio.create_task(
                world.endpoint.inbox.accept(world.envelope, world.identity)
            )
            await reached()
            # A read of the conversation answers while the event waits, even when the
            # wait is the event's own write holding the conversation lock.
            assert await asyncio.wait_for(world.harness.system.held_prompts("c"), 1) == ()
            if link_event != "none":
                await world.drop_link(link_event)
            release()

            receipt = await asyncio.wait_for(accept, SETTLES_WITHIN_SECONDS)
            assert receipt.disposition is ReceiptDisposition.accepted
            assert receipt.result == {"applied": True}
            assert await world.tool_rows() == 1
            await world.assert_nothing_held()

            # The Mac reconnects (if it has not yet) and resends the same head item.
            identity = (
                world.endpoint.runs.identity
                if link_event == "reconnected"
                else await world.reconnect()
            )
            assert identity is not None
            resend = await asyncio.wait_for(
                world.endpoint.inbox.accept(world.envelope, identity), SETTLES_WITHIN_SECONDS
            )
            assert resend == receipt
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                == receipt
            )
            assert await world.tool_rows() == 1
            await world.assert_nothing_held()

    asyncio.run(asyncio.wait_for(exercise(), 20))
    _no_save_trouble(caplog)


class _WireStub:
    """The transport's view of the current link: only its identity is read."""

    def __init__(self, identity: ExecutorIdentity) -> None:
        self.identity = identity

    def close(self, reason: str = "") -> None:
        raise AssertionError(f"retry later must not close the link: {reason}")


@pytest.mark.parametrize("wait_point", LIMITED_WAITS)
def test_a_mac_event_held_past_the_limit_is_told_to_retry_later_and_applies_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_point: str
) -> None:
    monkeypatch.setattr(vps_inbox, "MAC_ITEM_RETRY_LATER_SECONDS", TEST_RETRY_LATER_SECONDS)

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            running = world.state.running_turn
            assert running is not None
            reached, release = await _holds(monkeypatch)[wait_point](world)
            accept = asyncio.create_task(
                world.endpoint.inbox.accept(world.envelope, world.identity)
            )
            await reached()
            await world.drop_link("lost")

            with pytest.raises(PlannerError) as refused:
                await asyncio.wait_for(accept, SETTLES_WITHIN_SECONDS)
            assert refused.value.code is ErrorCode.retry_later
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

            # The reconnected Mac resends while the wait still holds. On the wire the
            # answer is the transport's ordinary error reply, and no task piles up.
            identity = await world.reconnect()
            stub = _WireStub(identity)
            world.endpoint.runs.link = cast(Link, stub)
            reply = await asyncio.wait_for(
                world.endpoint._handle(
                    WireMessage(WireMessageKind.envelope, "resend-1", as_object(world.envelope)),
                    cast(Link, stub),
                    world.endpoint._envelope,
                ),
                SETTLES_WITHIN_SECONDS,
            )
            assert reply is not None and reply.kind is WireMessageKind.error
            assert cast(dict[str, Any], reply.payload)["error"]["code"] == "retry_later"
            await world.no_task_left_behind()
            assert (
                lookup_receipt(world.store, world.envelope.sender_id, world.envelope.submission_id)
                is None
            )

            release()
            receipt = await asyncio.wait_for(
                world.endpoint.inbox.accept(world.envelope, identity), SETTLES_WITHIN_SECONDS
            )
            assert receipt.disposition is ReceiptDisposition.accepted
            assert receipt.result == {"applied": True}
            assert await world.endpoint.inbox.accept(world.envelope, identity) == receipt
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
            identity = await world.reconnect()
            receipt = await asyncio.wait_for(
                world.endpoint.inbox.accept(envelope, identity), SETTLES_WITHIN_SECONDS
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
            assert await world.endpoint.inbox.accept(envelope, identity) == receipt
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            preflights: list[object] = []
            original = world.endpoint.inbox.store.preflight

            def preflight(*args: Any, **kwargs: Any) -> Any:
                preflights.append(args)
                return original(*args, **kwargs)

            monkeypatch.setattr(world.endpoint.inbox.store, "preflight", preflight)
            running = world.state.running_turn
            # A new Mac process takes over: a new incarnation, not a newer generation.
            replacement = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "replacement", 1
            )
            await asyncio.to_thread(world.store.runs.begin_connection, replacement)

            with pytest.raises(PlannerError, match="incarnation has been replaced") as refused:
                await world.endpoint.inbox.accept(world.envelope, world.identity)
            assert refused.value.code is ErrorCode.validation
            assert preflights == []
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
            await world.drop_link("lost")
            world.state.lock.release()
            await asyncio.wait_for(world.state.backend_event_queue.join(), SETTLES_WITHIN_SECONDS)
            assert await world.tool_rows() == 1

    asyncio.run(asyncio.wait_for(exercise(), 20))
