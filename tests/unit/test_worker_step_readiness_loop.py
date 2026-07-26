"""Starting a worker step: the claim, the opener, the send, and what its fate does.

Everything here runs against a real temporary SQLite file and the in-memory conversation
system. The per-Ticket flow is a plain async function, so most of these drive it directly
with ``asyncio.run`` and no threads at all.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from typing import cast

import pytest

from planner.conversation.contracts import (
    ConversationStartRequest,
    ConversationSystem,
    PromptDeliveryFate,
    PromptDeliveryMode,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core.clock import TestClock
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.runtime import worker_step_readiness
from planner.runtime.worker_step_readiness_loop import (
    WorkerStepReadinessLoop,
    start_ready_worker_step,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, StageOwnershipMode, TicketStatus
from planner.worker_context import data as worker_context_data
from planner.worker_context.contracts import WorkerContextService
from planner.worker_context.service import SqliteWorkerContextService
from planner.worker_types.configuration import configured_worker_type_registry

FIXED_NOW = datetime(2026, 7, 6, 12, 0, 0).astimezone()
BOUNDARY_HOUR = 5
TODAY_DAY_ID = "day_2026-07-06"


# --- the world under test ------------------------------------------------------


class _World:
    """One temporary database, one conversation system, one clock."""

    def __init__(self, tmp_path: Path) -> None:
        self.db_path = str(tmp_path / "readiness-loop.db")
        with connect(self.db_path) as conn:
            create_schema(conn)
        self.clock = TestClock(FIXED_NOW)
        self.conversations = InMemoryConversationSystem()
        self.context = SqliteWorkerContextService(lambda: connect(self.db_path))

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def ready_ticket(
        self,
        *,
        title: str = "T",
        ownership_mode: StageOwnershipMode | None = None,
        conversation_id: str | None = None,
        on_today: bool = True,
    ) -> str:
        with self.connect() as conn:
            ticket = tickets_data.create_ticket(
                conn,
                worker_type="coding",
                title=title,
                actor="human",
                now=0,
                title_max_chars=200,
            )
            ticket = tickets_data.accept_proposal(
                conn,
                ticket.id,
                field="kickoff",
                actor="human",
                now=0,
                next_ceiling="none",
                at_cap=AtCap.propose,
            )
            if ownership_mode is not None:
                tickets_data.set_stage_ownership(
                    conn,
                    ticket.id,
                    stage=ticket.stage,
                    ownership_mode=ownership_mode,
                    now=0,
                )
            if conversation_id is not None:
                conn.execute(
                    "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                    (conversation_id, ticket.id),
                )
            if on_today:
                days_data.add_day_ticket(conn, TODAY_DAY_ID, ticket.id, 0)
        return ticket.id

    def start_conversation(self, conversation_id: str) -> None:
        asyncio.run(
            self.conversations.start_conversation(
                ConversationStartRequest(conversation_id=conversation_id)
            )
        )

    def add_pending_context(self, ticket_id: str, key: str, text: str) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            worker_context_data.set_context(conn, ticket_id, key, text)

    def pending_context_keys(self, ticket_id: str) -> list[str]:
        with self.connect() as conn:
            return [
                str(row["context_key"])
                for row in conn.execute(
                    "SELECT context_key FROM pending_worker_context "
                    "WHERE worker_entity_id = ? ORDER BY context_key",
                    (ticket_id,),
                ).fetchall()
            ]

    def ticket(self, ticket_id: str):
        with self.connect() as conn:
            return tickets_data.read_ticket(conn, ticket_id)

    def start_step(self, ticket_id: str) -> bool:
        return asyncio.run(
            start_ready_worker_step(
                ticket_id,
                connect_database=self.connect,
                conversation_system=cast(ConversationSystem, self.conversations),
                worker_context_service=cast(WorkerContextService, self.context),
                worker_type_registry=configured_worker_type_registry(),
                planning_day_id_resolver=lambda: TODAY_DAY_ID,
                now=self.clock.now_unix,
            )
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


# --- the claim -----------------------------------------------------------------


def test_two_racing_claimers_on_one_database_produce_exactly_one_winner(
    tmp_path: Path,
) -> None:
    world = _World(tmp_path)
    ticket_id = world.ready_ticket()
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []
    outcomes_lock = threading.Lock()

    def claim() -> None:
        conn = connect(world.db_path)
        try:
            barrier.wait(5)
            claimed = tickets_data.claim_ticket_for_worker_step(
                conn,
                ticket_id,
                planning_day_id_resolver=lambda: TODAY_DAY_ID,
                readiness_check=worker_step_readiness.is_ready_for_worker_step,
                now=1,
            )
        finally:
            conn.close()
        with outcomes_lock:
            outcomes.append(claimed is not None)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()

    assert sorted(outcomes) == [False, True]
    assert world.ticket(ticket_id).ticket_status is TicketStatus.agent


def test_a_release_does_not_fire_once_the_status_has_been_written_again(
    tmp_path: Path,
) -> None:
    # A delayed release carries the status AND the moment it was written. The status can
    # legitimately come back to the same value; the moment cannot — unless both flips land
    # in the same second, which is the accepted limitation of this guard: the stamp has
    # one-second resolution, so a claim and a later re-claim inside the same second are
    # indistinguishable to it. Recorded, and not worth a wider clock to close.
    world = _World(tmp_path)
    ticket_id = world.ready_ticket()
    conn = world.connect()
    try:
        claimed = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=100,
        )
        assert claimed is not None

        # Someone else takes the Ticket away and hands it back to the worker later.
        assert tickets_data.release_worker_step_claim(
            conn,
            ticket_id,
            expected_status=claimed.ticket_status,
            expected_status_changed_at=claimed.ticket_status_changed_at,
            now=110,
        )
        reclaimed = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=120,
        )
        assert reclaimed is not None
        assert reclaimed.ticket_status is claimed.ticket_status

        # The first claim's late release finds the same status and a different moment.
        assert (
            tickets_data.release_worker_step_claim(
                conn,
                ticket_id,
                expected_status=claimed.ticket_status,
                expected_status_changed_at=claimed.ticket_status_changed_at,
                now=130,
            )
            is False
        )
    finally:
        conn.close()
    assert world.ticket(ticket_id).ticket_status is TicketStatus.agent


# --- the per-Ticket flow -------------------------------------------------------


def test_an_occupied_worker_is_skipped_without_touching_the_ticket(world: _World) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-busy")
    world.start_conversation("conv-busy")
    asyncio.run(
        world.conversations.send(
            "conv-busy",
            text_message_content("already working"),
            sender_label="loop",
        )
    )

    assert world.start_step(ticket_id) is False
    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty
    # Only the turn that was already running ever reached the backend.
    assert len(world.conversations.backend_prompt_writes("conv-busy")) == 1


def test_a_refused_send_gives_the_claim_back_and_says_so_once(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-refuse")
    world.start_conversation("conv-refuse")
    world.conversations.arm_backend_write_failure("conv-refuse")
    world.add_pending_context(ticket_id, "ticket_changed", "The user renamed the ticket.")

    with caplog.at_level(logging.ERROR, logger="planner.runtime.worker_step_readiness_loop"):
        assert world.start_step(ticket_id) is False

    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert ticket_id in message
    assert "conv-refuse" in message
    assert "write_to_backend_failed" in message
    # A refused delivery reached nobody, so the context is still owed.
    assert world.pending_context_keys(ticket_id) == ["ticket_changed"]


def test_a_queued_send_counts_as_a_success(world: _World) -> None:
    # The occupancy pre-check is the loop's own; a collision that slips past it queues,
    # and a held message is delivered work, not a failure.
    ticket_id = world.ready_ticket(conversation_id="conv-queue")
    world.start_conversation("conv-queue")
    world.add_pending_context(ticket_id, "ticket_changed", "The user renamed the ticket.")

    conn = world.connect()
    try:
        claimed = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=1,
        )
        assert claimed is not None
    finally:
        conn.close()
    # Put the Ticket back so the flow's own claim succeeds, then make the worker busy
    # between the occupancy check and the send.
    conn = world.connect()
    try:
        assert tickets_data.release_worker_step_claim(
            conn,
            ticket_id,
            expected_status=claimed.ticket_status,
            expected_status_changed_at=claimed.ticket_status_changed_at,
            now=2,
        )
    finally:
        conn.close()

    queueing = _QueueingConversationSystem(world.conversations, "conv-queue")
    started = asyncio.run(
        start_ready_worker_step(
            ticket_id,
            connect_database=world.connect,
            conversation_system=cast(ConversationSystem, queueing),
            worker_context_service=cast(WorkerContextService, world.context),
            worker_type_registry=configured_worker_type_registry(),
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            now=world.clock.now_unix,
        )
    )

    assert started is True
    # Held, not written: only the colliding turn reached the backend, and the opener is
    # waiting behind it. That is a delivery, so nothing is reverted or re-owed.
    writes = world.conversations.backend_prompt_writes("conv-queue")
    assert [write.sender_label for write in writes] == ["browser"]
    assert world.ticket(ticket_id).ticket_status is TicketStatus.agent
    assert world.pending_context_keys(ticket_id) == []


class _QueueingConversationSystem:
    """Idle when asked, busy by the time the send arrives."""

    def __init__(self, system: InMemoryConversationSystem, conversation_id: str) -> None:
        self._system = system
        self._conversation_id = conversation_id

    async def is_running(self, conversation_id: str) -> bool:
        return False

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
    ) -> PromptDeliveryFate:
        if not await self._system.is_running(self._conversation_id):
            await self._system.send(
                self._conversation_id,
                text_message_content("collision"),
                sender_label="browser",
            )
        return await self._system.send(
            conversation_id,
            text_message_content(text),
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
        )

    async def interrupt(self, conversation_id: str) -> None:
        await self._system.interrupt(conversation_id)

    async def kill(self, conversation_id: str) -> None:
        await self._system.kill(conversation_id)

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return await self._system.has_pending_permission_ask(conversation_id)


def test_pending_context_is_acknowledged_only_after_the_send_lands(world: _World) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-context")
    world.start_conversation("conv-context")
    world.add_pending_context(ticket_id, "ticket_changed", "The user renamed the ticket.")
    world.add_pending_context(ticket_id, "day_changed", "The ticket moved to today.")
    assert world.pending_context_keys(ticket_id) == ["day_changed", "ticket_changed"]

    assert world.start_step(ticket_id) is True
    assert world.pending_context_keys(ticket_id) == []


class _AcknowledgementRefusingContext:
    """Prepares as usual, then cannot tick the context off."""

    def __init__(self, service: WorkerContextService) -> None:
        self._service = service

    def prepare(self, worker_entity_id: str, prompt_text: str):
        return self._service.prepare(worker_entity_id, prompt_text)

    def acknowledge(self, worker_entity_id: str, receipts) -> None:
        raise RuntimeError("the context store is unreachable")


def test_a_failed_acknowledgement_after_a_delivery_is_reported_and_never_reverted(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    # The text is out. Reverting here would re-arm the Ticket and send it twice, so the
    # failure is reported and the claim stands — the context stays owed instead.
    ticket_id = world.ready_ticket(conversation_id="conv-ack")
    world.start_conversation("conv-ack")
    world.add_pending_context(ticket_id, "ticket_changed", "The user renamed the ticket.")

    with caplog.at_level(logging.ERROR, logger="planner.runtime.worker_step_readiness_loop"):
        started = asyncio.run(
            start_ready_worker_step(
                ticket_id,
                connect_database=world.connect,
                conversation_system=cast(ConversationSystem, world.conversations),
                worker_context_service=cast(
                    WorkerContextService, _AcknowledgementRefusingContext(world.context)
                ),
                worker_type_registry=configured_worker_type_registry(),
                planning_day_id_resolver=lambda: TODAY_DAY_ID,
                now=world.clock.now_unix,
            )
        )

    assert started is True
    assert world.ticket(ticket_id).ticket_status is TicketStatus.agent
    assert len(world.conversations.backend_prompt_writes("conv-ack")) == 1
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert ticket_id in errors[0].getMessage()
    assert errors[0].exc_info is not None
    assert world.pending_context_keys(ticket_id) == ["ticket_changed"]


class _UnreadableConversationSystem(InMemoryConversationSystem):
    """A conversation system whose liveness read fails outright."""

    async def is_running(self, conversation_id: str) -> bool:
        raise RuntimeError("the conversation system is unreachable")


def test_a_step_that_fails_outside_the_flows_own_handling_is_still_reported(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    # Nobody awaits a scheduled step, and the occupancy read happens before the flow has
    # anything to give back, so it sits outside the flow's own failure handling. An
    # exception there has no way to be heard except through the finished task itself.
    ticket_id = world.ready_ticket(conversation_id="conv-unreadable")
    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(
        world,
        conversation_system=cast(ConversationSystem, _UnreadableConversationSystem()),
    )
    with caplog.at_level(logging.ERROR, logger="planner.runtime.worker_step_readiness_loop"):
        try:
            assert readiness_loop.poll_once() == [ticket_id]
            assert _waited_for(lambda: bool(caplog.records))
        finally:
            readiness_loop.stop()
            asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
            thread.join(5)
            asyncio_loop.close()

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert ticket_id in errors[0].getMessage()
    assert errors[0].exc_info is not None
    # Nothing was claimed, so the Ticket is exactly where it was.
    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty


def test_the_opener_carries_the_step_prompt_and_the_pending_context(world: _World) -> None:
    ticket_id = world.ready_ticket(title="Ship it", conversation_id="conv-opener")
    world.start_conversation("conv-opener")
    world.add_pending_context(ticket_id, "ticket_changed", "The user renamed the ticket.")

    assert world.start_step(ticket_id) is True

    writes = world.conversations.backend_prompt_writes("conv-opener")
    assert len(writes) == 1
    assert writes[0].sender_label == "loop"
    assert writes[0].mode is PromptDeliveryMode.run_when_free
    assert f"Work ticket {ticket_id} — Ship it" in writes[0].text
    assert "propose the 'success' field for approval" in writes[0].text
    assert "Stage owner: worker" in writes[0].text
    assert "The user renamed the ticket." in writes[0].text


def test_a_paired_owned_stage_departs_at_paired_and_gets_the_paired_opener(
    world: _World,
) -> None:
    ticket_id = world.ready_ticket(
        title="Talk it through",
        ownership_mode=StageOwnershipMode.paired,
        conversation_id="conv-paired",
    )
    world.start_conversation("conv-paired")

    assert world.start_step(ticket_id) is True

    assert world.ticket(ticket_id).ticket_status is TicketStatus.paired
    text = world.conversations.backend_prompt_writes("conv-paired")[0].text
    assert "open the paired discussion for the 'success' field" in text
    assert "Stage owner: paired" in text


def test_an_unlinked_ticket_gets_a_conversation_and_the_first_message(world: _World) -> None:
    ticket_id = world.ready_ticket(title="First message")
    assert world.ticket(ticket_id).conversation_id is None

    assert world.start_step(ticket_id) is True

    ticket = world.ticket(ticket_id)
    conversation_id = ticket.conversation_id
    assert conversation_id is not None
    assert conversation_id.startswith("conv_")
    assert ticket.ticket_status is TicketStatus.agent
    # The conversation exists, is addressable, and has the opener on its wire.
    assert asyncio.run(world.conversations.is_running(conversation_id)) is True
    writes = world.conversations.backend_prompt_writes(conversation_id)
    assert len(writes) == 1
    assert f"Work ticket {ticket_id} — First message" in writes[0].text
    # The last-chosen columns record what the conversation actually runs on.
    assert ticket.employee_backend in ("hermes", "codex", "claude")


def test_a_ticket_that_is_not_ready_is_never_sent_to(world: _World) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-unready", on_today=False)
    world.start_conversation("conv-unready")

    assert world.start_step(ticket_id) is False
    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty
    assert world.conversations.backend_prompt_writes("conv-unready") == ()


# --- the polling loop ----------------------------------------------------------


class _HeldAtTheOccupancyCheck:
    """The fake, with its first read held open until a test lets it go.

    The occupancy check is the flow's first await, so holding it there keeps a step
    genuinely in flight while its Ticket is still untouched and still ready.
    """

    def __init__(self, system: InMemoryConversationSystem) -> None:
        self._system = system
        self.reached = threading.Event()
        self._gate: asyncio.Event | None = None

    def release(self, asyncio_loop: asyncio.AbstractEventLoop) -> None:
        gate = self._gate
        if gate is not None:
            asyncio_loop.call_soon_threadsafe(gate.set)

    async def is_running(self, conversation_id: str) -> bool:
        if self._gate is None:
            self._gate = asyncio.Event()
        self.reached.set()
        await self._gate.wait()
        return await self._system.is_running(conversation_id)

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
    ) -> PromptDeliveryFate:
        return await self._system.send(
            conversation_id,
            text_message_content(text),
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
        )

    async def interrupt(self, conversation_id: str) -> None:
        await self._system.interrupt(conversation_id)

    async def kill(self, conversation_id: str) -> None:
        await self._system.kill(conversation_id)

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return await self._system.has_pending_permission_ask(conversation_id)


def _loop_in_a_thread(
    world: _World,
    *,
    conversation_system: ConversationSystem | None = None,
) -> tuple[WorkerStepReadinessLoop, asyncio.AbstractEventLoop, threading.Thread]:
    asyncio_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=asyncio_loop.run_forever, daemon=True)
    thread.start()
    return (
        WorkerStepReadinessLoop(
            world.db_path,
            world.clock,
            conversation_system=(
                cast(ConversationSystem, world.conversations)
                if conversation_system is None
                else conversation_system
            ),
            worker_context_service=cast(WorkerContextService, world.context),
            asyncio_loop=asyncio_loop,
            boundary_hour=BOUNDARY_HOUR,
        ),
        asyncio_loop,
        thread,
    )


def _waited_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = monotonic() + timeout
    while monotonic() < end:
        if predicate():
            return True
        sleep(0.01)
    return False


def test_the_poll_schedules_every_ready_ticket_and_skips_the_rest(world: _World) -> None:
    ready_one = world.ready_ticket(title="One")
    ready_two = world.ready_ticket(title="Two")
    world.ready_ticket(title="Not today", on_today=False)
    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        scheduled = readiness_loop.poll_once()
        assert sorted(scheduled) == sorted([ready_one, ready_two])
        assert _waited_for(
            lambda: world.ticket(ready_one).conversation_id is not None
            and world.ticket(ready_two).conversation_id is not None
        )
        assert world.ticket(ready_one).ticket_status is TicketStatus.agent
        assert world.ticket(ready_two).ticket_status is TicketStatus.agent
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()


def test_one_closeout_lane_takes_one_ticket_per_pass(world: _World) -> None:
    first = world.ready_ticket(title="Closeout one")
    second = world.ready_ticket(title="Closeout two")
    with world.connect() as conn:
        for ticket_id in (first, second):
            tickets_data.set_stage(
                conn, ticket_id, new_stage="needs_closeout", actor="human", now=0
            )
        conn.execute("UPDATE tickets SET updated_at = 10 WHERE id = ?", (first,))
        conn.execute("UPDATE tickets SET updated_at = 20 WHERE id = ?", (second,))
    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        # The lane looks free to both waiters until one of them actually flips, so the
        # poll itself has to hold the lane for the one it picks.
        assert readiness_loop.poll_once() == [first]
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()


def test_a_ticket_already_in_flight_is_not_scheduled_twice(world: _World) -> None:
    # The step is held at its very first await, before the claim, so the Ticket is still
    # plainly ready when the second poll runs. Nothing but the in-flight set can turn that
    # poll away, which is the point: without it a stalled step would be started twice.
    ticket_id = world.ready_ticket(conversation_id="conv-inflight")
    world.start_conversation("conv-inflight")
    held = _HeldAtTheOccupancyCheck(world.conversations)
    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(
        world, conversation_system=cast(ConversationSystem, held)
    )
    try:
        assert readiness_loop.poll_once() == [ticket_id]
        assert held.reached.wait(5)
        assert world.ticket(ticket_id).ticket_status is TicketStatus.empty

        assert readiness_loop.poll_once() == []

        held.release(asyncio_loop)
        # Wait for the opener to reach the backend, not for the claim. The claim is the
        # status flip and it happens strictly before the send, so waiting on the status
        # can return while the send is still in the air — which is what made this test
        # fail about one run in twenty.
        assert _waited_for(
            lambda: len(world.conversations.backend_prompt_writes("conv-inflight")) == 1
        )
        assert world.ticket(ticket_id).ticket_status is TicketStatus.agent
        # One step ran, so one opener reached the backend.
        assert len(world.conversations.backend_prompt_writes("conv-inflight")) == 1
    finally:
        held.release(asyncio_loop)
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()


def test_a_wake_makes_the_running_loop_poll_before_its_timer(world: _World) -> None:
    ticket_id = world.ready_ticket()
    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        readiness_loop.start(3600)
        assert _waited_for(lambda: world.ticket(ticket_id).ticket_status is TicketStatus.agent)

        second = world.ready_ticket(title="Woken")
        readiness_loop.wake()
        assert _waited_for(lambda: world.ticket(second).ticket_status is TicketStatus.agent)
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()


def test_the_test_mode_route_runs_one_worker_step_against_the_composed_system(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app

    world = _World(tmp_path)
    ticket_id = world.ready_ticket(title="Driven by hand")
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": world.db_path,
            "PLAN_FAKE_NOW": FIXED_NOW.isoformat(),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        world.connect,
        # This asserts what the step wrote to the backend, so it needs a conversation
        # system that records its writes rather than one that spawns an agent.
        conversation_system_for_test=InMemoryConversationSystem(),
    )

    with TestClient(app) as client:
        response = client.post(f"/api/test/run-step/{ticket_id}")
        assert response.status_code == 200, response.text
        assert response.json() == {"dispatched": True, "ticket_id": ticket_id}
        conversation_id = world.ticket(ticket_id).conversation_id
        assert conversation_id is not None
        writes = app.state.conversation_system.backend_prompt_writes(conversation_id)
        assert len(writes) == 1
        assert writes[0].sender_label == "loop"

    assert world.ticket(ticket_id).ticket_status is TicketStatus.agent
