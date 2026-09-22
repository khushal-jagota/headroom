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
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.ticket_progress import advance_ticket

from planner.conversation.contracts import (
    ConversationStartRequest,
    ConversationSystem,
    HeldPrompt,
    PromptDeliveryFate,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import MessageContent, text_message_content
from planner.core.clock import TestClock
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.runtime import worker_step_readiness
from planner.runtime.logic.worker_step_prompt import (
    MEMORY_LOSS_NOTICE,
    READ_YOUR_TICKET_COMMAND,
)
from planner.runtime.worker_step_readiness_loop import (
    WorkerStepDeliveryFate,
    WorkerStepReadinessLoop,
    WorkerStepStartResult,
    start_ready_worker_step,
)
from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback, worker_restart
from planner.tickets.contracts import Ticket, TicketEdit, TicketStatus, WorkerStepClaim
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

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def ready_ticket(
        self,
        *,
        title: str = "T",
        kickoff_note: str = "Agreed brief.",
        worker_type: str = "coding",
        conversation_id: str | None = None,
        on_today: bool = True,
    ) -> str:
        with self.connect() as conn:
            ticket = tickets_data.create_ticket(
                conn,
                worker_type=worker_type,
                title=title,
                kickoff_note=kickoff_note,
                principal=OWNER_PRINCIPAL,
                now=0,
                title_max_chars=200,
            )
            ticket = tickets_data.accept_proposal(
                conn,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=0,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )
            if conversation_id is not None:
                conn.execute(
                    "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                    (conversation_id, ticket.id),
                )
            if on_today:
                days_data.add_day_ticket(conn, TODAY_DAY_ID, ticket.id, 0)
        return ticket.id

    def record_compacted_conversation(
        self,
        conversation_id: str,
        *,
        last_worker_step_sequence: int,
        compacted_through: int,
    ) -> None:
        """Write the conversation rows the in-memory system does not keep.

        The boundary is read straight off the conversation record, so a test that wants
        one states it: a worker-step message at one sequence, and a compaction past it.
        """
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, "
                "access, created_at, automatically_compacted_through_sequence) VALUES "
                "(?, 'claude', '/tmp', 'full', 0, ?)",
                (conversation_id, compacted_through),
            )
            conn.execute(
                "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
                "created_at) VALUES (?, ?, 'prompt', ?, 0)",
                (
                    conversation_id,
                    last_worker_step_sequence,
                    '{"sender_message_id": "worker_step_message_earlier"}',
                ),
            )

    def remove_from_today(self, ticket_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
                (TODAY_DAY_ID, ticket_id),
            )

    def held_prompts(self, conversation_id: str) -> tuple[HeldPrompt, ...]:
        return asyncio.run(self.conversations.held_prompts(conversation_id))

    def start_conversation(self, conversation_id: str) -> None:
        asyncio.run(
            self.conversations.start_conversation(
                ConversationStartRequest(conversation_id=conversation_id, model="a-model")
            )
        )

    def add_revision_feedback(self, ticket_id: str, message: str) -> None:
        with self.connect() as conn:
            ticket = tickets_data.read_ticket(conn, ticket_id)
            revision_feedback.set_feedback(
                conn,
                ticket_id,
                stage=ticket.stage,
                sender=OWNER_PRINCIPAL,
                message=message,
                now=1,
            )

    def pending_revision_feedback(self, ticket_id: str) -> bool:
        with self.connect() as conn:
            return revision_feedback.snapshot(conn, ticket_id) is not None

    def ticket(self, ticket_id: str) -> Ticket:
        with self.connect() as conn:
            return tickets_data.read_ticket(conn, ticket_id)

    def skill_bindings(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT sender_message_id, skill_role, binding_status "
                "FROM worker_step_skill_bindings ORDER BY skill_role"
            ).fetchall()

    def start_step(self, ticket_id: str) -> WorkerStepStartResult:
        return asyncio.run(
            start_ready_worker_step(
                ticket_id,
                connect_database=self.connect,
                conversation_system=cast(ConversationSystem, self.conversations),
                worker_type_registry=configured_worker_type_registry(),
                planning_day_id_resolver=lambda: TODAY_DAY_ID,
                now=self.clock.now_unix,
            )
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.mark.parametrize(
    ("fate", "started", "delivery_fate"),
    [
        (PromptDeliveryStarted(), True, "started"),
        (PromptDeliveryQueued(queue_position=3), True, "queued"),
        (PromptDeliveryInjected(), True, "injected"),
        (
            PromptDeliveryRefused(PromptDeliveryRefusalReason.write_to_backend_failed),
            False,
            "refused",
        ),
        (PromptDeliveryUncertain(), False, "uncertain"),
    ],
)
def test_worker_step_start_result_keeps_each_delivery_fate(
    fate: PromptDeliveryFate, started: bool, delivery_fate: WorkerStepDeliveryFate
) -> None:
    assert WorkerStepStartResult.from_delivery_fate(fate) == WorkerStepStartResult(
        started=started,
        delivery_fate=delivery_fate,
    )


@pytest.mark.parametrize(
    ("started", "delivery_fate"),
    [
        (True, None),
        (True, "uncertain"),
        (False, "started"),
    ],
)
def test_worker_step_start_result_rejects_a_false_success_claim(
    started: bool, delivery_fate: WorkerStepDeliveryFate | None
) -> None:
    with pytest.raises(ValueError, match="started must match the delivery fate"):
        WorkerStepStartResult(started=started, delivery_fate=delivery_fate)


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

    assert world.start_step(ticket_id) == WorkerStepStartResult(started=False)
    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty
    # Only the turn that was already running ever reached the backend.
    assert len(world.conversations.backend_prompt_writes("conv-busy")) == 1


def test_a_refused_send_gives_the_claim_back_and_says_so_once(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-refuse")
    world.start_conversation("conv-refuse")
    world.conversations.arm_backend_write_failure("conv-refuse")

    with caplog.at_level(logging.ERROR, logger="planner.runtime.worker_step_readiness_loop"):
        result = world.start_step(ticket_id)

    assert result.started is False
    assert result.delivery_fate == "refused"

    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert ticket_id in message
    assert "conv-refuse" in message
    assert "write_to_backend_failed" in message
    assert world.skill_bindings() == []


def test_an_uncertain_send_marks_the_exact_claim_errored_for_supervisor_restart(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-uncertain")
    world.start_conversation("conv-uncertain")

    with caplog.at_level(logging.ERROR, logger="planner.runtime.worker_step_readiness_loop"):
        started = asyncio.run(
            start_ready_worker_step(
                ticket_id,
                connect_database=world.connect,
                conversation_system=cast(
                    ConversationSystem,
                    _UncertainConversationSystem(world.conversations),
                ),
                worker_type_registry=configured_worker_type_registry(),
                planning_day_id_resolver=lambda: TODAY_DAY_ID,
                now=world.clock.now_unix,
            )
        )

    assert started.started is False
    assert started.delivery_fate == "uncertain"
    ticket = world.ticket(ticket_id)
    assert ticket.worker_step_claim is WorkerStepClaim.errored
    assert ticket.ticket_status is TicketStatus.errored
    assert world.conversations.backend_prompt_writes("conv-uncertain") == ()
    assert world.skill_bindings() == []
    with world.connect() as conn:
        assert worker_restart.require_restartable(conn, OWNER_PRINCIPAL, ticket_id).id == ticket_id
        uncertain_restart = asyncio.run(
            worker_restart.restart_worker(
                world.conversations,
                conn,
                OWNER_PRINCIPAL,
                ticket_id,
                write_employee_configuration=None,
                start_worker_step=lambda planning_day_id: start_ready_worker_step(
                    ticket_id,
                    connect_database=world.connect,
                    conversation_system=cast(
                        ConversationSystem,
                        _UncertainConversationSystem(world.conversations),
                    ),
                    worker_type_registry=configured_worker_type_registry(),
                    planning_day_id_resolver=lambda: planning_day_id,
                    now=world.clock.now_unix,
                ),
                resolve_planning_write=lambda: (TODAY_DAY_ID, world.clock.now_unix()),
                now=world.clock.now_unix,
            )
        )
        assert uncertain_restart["started"] is False
        assert uncertain_restart["delivery_fate"] == "uncertain"
        assert uncertain_restart["not_started_because"] is None
        assert world.ticket(ticket_id).worker_step_claim is WorkerStepClaim.errored

        restarted = asyncio.run(
            worker_restart.restart_worker(
                world.conversations,
                conn,
                OWNER_PRINCIPAL,
                ticket_id,
                write_employee_configuration=None,
                start_worker_step=lambda planning_day_id: start_ready_worker_step(
                    ticket_id,
                    connect_database=world.connect,
                    conversation_system=cast(ConversationSystem, world.conversations),
                    worker_type_registry=configured_worker_type_registry(),
                    planning_day_id_resolver=lambda: planning_day_id,
                    now=world.clock.now_unix,
                ),
                resolve_planning_write=lambda: (TODAY_DAY_ID, world.clock.now_unix()),
                now=world.clock.now_unix,
            )
        )
    assert restarted["started"] is True
    assert restarted["delivery_fate"] == "started"
    assert world.ticket(ticket_id).worker_step_claim is WorkerStepClaim.out
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 2
    assert all("delivery was uncertain" in error.getMessage() for error in errors)


def test_an_uncertain_first_opener_stays_errored_without_a_live_worker(world: _World) -> None:
    ticket_id = world.ready_ticket()

    started = asyncio.run(
        start_ready_worker_step(
            ticket_id,
            connect_database=world.connect,
            conversation_system=cast(
                ConversationSystem,
                _UncertainConversationSystem(world.conversations),
            ),
            worker_type_registry=configured_worker_type_registry(),
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            now=world.clock.now_unix,
        )
    )

    assert started.started is False
    assert started.delivery_fate == "uncertain"
    ticket = world.ticket(ticket_id)
    assert ticket.worker_step_claim is WorkerStepClaim.errored
    assert ticket.conversation_id is not None
    assert asyncio.run(world.conversations.is_running(ticket.conversation_id)) is False
    assert world.conversations.backend_prompt_writes(ticket.conversation_id) == ()


def test_an_old_uncertain_delivery_cannot_error_a_newer_claim(world: _World) -> None:
    ticket_id = world.ready_ticket()
    with world.connect() as conn:
        first_claim = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=1,
        )
        assert first_claim is not None
        assert tickets_data.release_worker_step_claim(
            conn,
            ticket_id,
            expected_claim=first_claim.worker_step_claim,
            expected_claim_revision=first_claim.worker_step_claim_revision,
            now=2,
        )
        newer_claim = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=3,
        )
        assert newer_claim is not None

        assert not tickets_data.mark_worker_step_claim_errored(
            conn,
            ticket_id,
            expected_claim=first_claim.worker_step_claim,
            expected_claim_revision=first_claim.worker_step_claim_revision,
            now=4,
        )

    ticket = world.ticket(ticket_id)
    assert ticket.worker_step_claim is WorkerStepClaim.out
    assert ticket.worker_step_claim_revision == newer_claim.worker_step_claim_revision


class _UncertainConversationSystem:
    """Report an ambiguous send without admitting the worker-step opener."""

    def __init__(self, system: InMemoryConversationSystem) -> None:
        self._system = system

    async def is_running(self, conversation_id: str) -> bool:
        return await self._system.is_running(conversation_id)

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> PromptDeliveryFate:
        return PromptDeliveryUncertain()


def test_revision_feedback_is_consumed_only_after_an_actual_worker_send(world: _World) -> None:
    ticket_id = world.ready_ticket(conversation_id="conv-revision-feedback")
    world.start_conversation("conv-revision-feedback")
    world.add_revision_feedback(ticket_id, "  Preserve this exact feedback.  ")
    with world.connect() as conn:
        tickets_data.edit_ticket(
            conn,
            ticket_id,
            edit=TicketEdit(guidance="Mutable guidance changed independently."),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=2,
        )
    world.conversations.arm_backend_write_failure("conv-revision-feedback")

    assert world.start_step(ticket_id).started is False
    assert world.pending_revision_feedback(ticket_id) is True
    world.conversations._conversations[  # noqa: SLF001 - focused failure recovery proof
        "conv-revision-feedback"
    ].armed_backend_write_failure = False
    assert world.start_step(ticket_id).started is True

    writes = world.conversations.backend_prompt_writes("conv-revision-feedback")
    assert len(writes) == 1
    assert "Revision feedback from owner owner for stage needs_success_condition" in writes[0].text
    assert "  Preserve this exact feedback.  " in writes[0].text
    # Guidance moves on its own and the worker reads it off the Ticket, so a rejection
    # carries only the feedback that explains the rejection.
    assert "Mutable guidance changed independently." not in writes[0].text
    assert world.pending_revision_feedback(ticket_id) is False


def test_a_refused_user_owned_opener_rearms_the_stage(world: _World) -> None:
    ticket_id = world.ready_ticket(
        worker_type="new_worker",
        conversation_id="conv-paired-refuse",
    )
    world.start_conversation("conv-paired-refuse")
    world.conversations.arm_backend_write_failure("conv-paired-refuse")

    assert world.start_step(ticket_id).started is False
    assert world.ticket(ticket_id).ticket_status is TicketStatus.empty
    with world.connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            is None
        )
        assert worker_step_readiness.is_ready_for_worker_step(
            conn,
            tickets_data.read_ticket(conn, ticket_id),
            planning_day_id=TODAY_DAY_ID,
            worker_type_definition=configured_worker_type_registry().require("new_worker"),
        )


def test_a_queued_send_counts_as_a_success(world: _World) -> None:
    # The occupancy pre-check is the loop's own; a collision that slips past it queues,
    # and a held message is delivered work, not a failure.
    ticket_id = world.ready_ticket(conversation_id="conv-queue")
    world.start_conversation("conv-queue")
    world.add_revision_feedback(ticket_id, "Queueing still delivers this feedback.")

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
            expected_claim=claimed.worker_step_claim,
            expected_claim_revision=claimed.worker_step_claim_revision,
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
            worker_type_registry=configured_worker_type_registry(),
            planning_day_id_resolver=lambda: TODAY_DAY_ID,
            now=world.clock.now_unix,
        )
    )

    assert started.started is True
    assert started.delivery_fate == "queued"
    # Held, not written: only the colliding turn reached the backend, and the opener is
    # waiting behind it. That is a delivery, so nothing is reverted or re-owed.
    writes = world.conversations.backend_prompt_writes("conv-queue")
    assert [write.sender_label for write in writes] == ["browser"]
    assert world.ticket(ticket_id).ticket_status is TicketStatus.agent
    assert world.pending_revision_feedback(ticket_id) is False
    assert {row["binding_status"] for row in world.skill_bindings()} == {"provisional"}


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
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> PromptDeliveryFate:
        if not await self._system.is_running(self._conversation_id):
            await self._system.send(
                self._conversation_id,
                text_message_content("collision"),
                sender_label="browser",
            )
        return await self._system.send(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        )

    async def interrupt(self, conversation_id: str) -> None:
        await self._system.interrupt(conversation_id)

    async def kill(self, conversation_id: str) -> None:
        await self._system.kill(conversation_id)

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return await self._system.has_pending_permission_ask(conversation_id)


def test_the_opener_carries_only_what_the_worker_cannot_get_for_itself(world: _World) -> None:
    ticket_id = world.ready_ticket(
        title="Ship it",
        kickoff_note="Use this agreed starting point.",
        conversation_id="conv-opener",
    )
    world.start_conversation("conv-opener")
    guidance = "Keep the owner’s boundary.\n\n  Exact whitespace stays.  "
    with world.connect() as conn:
        tickets_data.edit_ticket(
            conn,
            ticket_id,
            edit=TicketEdit(guidance=guidance),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=0,
        )
    assert world.start_step(ticket_id).started is True

    writes = world.conversations.backend_prompt_writes("conv-opener")
    assert len(writes) == 1
    assert writes[0].sender_label == "loop"
    assert writes[0].mode is PromptDeliveryMode.queue
    sender_message_id = world.conversations.observations("conv-opener")[-1].sender_message_id
    assert sender_message_id is not None
    assert f"Work ticket {ticket_id} — Ship it" in writes[0].text
    assert "propose the 'success_condition' field for approval" in writes[0].text
    assert "Stage owner: worker" in writes[0].text
    assert f"Read your Ticket first: {READ_YOUR_TICKET_COMMAND}." in writes[0].text
    # The Ticket is a read the worker makes, so neither of these rides the message.
    assert "Ticket guidance" not in writes[0].text
    assert "Exact whitespace stays" not in writes[0].text
    assert "Ticket brief" not in writes[0].text
    assert "Use this agreed starting point." not in writes[0].text
    assert MEMORY_LOSS_NOTICE not in writes[0].text
    bindings = world.skill_bindings()
    assert len(bindings) == 3
    assert {row["sender_message_id"] for row in bindings} == {sender_message_id}


def test_a_wake_after_a_compaction_leads_with_the_memory_loss_notice(world: _World) -> None:
    ticket_id = world.ready_ticket(title="Ship it", conversation_id="conv-compacted")
    world.start_conversation("conv-compacted")
    world.record_compacted_conversation(
        "conv-compacted", last_worker_step_sequence=4, compacted_through=9
    )

    assert world.start_step(ticket_id).started is True

    text = world.conversations.backend_prompt_writes("conv-compacted")[0].text
    assert MEMORY_LOSS_NOTICE in text
    assert f"Work ticket {ticket_id} — Ship it" in text
    # It leads: a worker reads why it is confused before it reads what to do.
    assert text.index(MEMORY_LOSS_NOTICE) < text.index("Work ticket")


def test_a_worker_compacted_part_way_through_a_step_is_told_without_waiting_for_a_wake(
    world: _World,
) -> None:
    ticket_id = world.ready_ticket(title="Ship it", conversation_id="conv-mid-step")
    world.start_conversation("conv-mid-step")
    assert world.start_step(ticket_id).started is True
    assert world.ticket(ticket_id).worker_step_claim is WorkerStepClaim.out
    # The step is still out, and the worker is between turns. That is when Panels
    # compacts an idle worker, and it is the case a later wake never reaches.
    world.conversations.complete_running_turn("conv-mid-step")
    world.record_compacted_conversation(
        "conv-mid-step", last_worker_step_sequence=4, compacted_through=9
    )

    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        assert readiness_loop.tell_every_worker_that_lost_its_memory() == [ticket_id]
        assert _waited_for(
            lambda: len(world.conversations.backend_prompt_writes("conv-mid-step")) == 2
        )
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()

    notice = world.conversations.backend_prompt_writes("conv-mid-step")[1]
    assert MEMORY_LOSS_NOTICE in notice.text
    assert f"part-way through a step on ticket {ticket_id}" in notice.text
    assert notice.mode is PromptDeliveryMode.queue
    # The step keeps its claim: the worker was never sent away, only told.
    assert world.ticket(ticket_id).worker_step_claim is WorkerStepClaim.out


def test_a_notice_still_queued_behind_a_busy_worker_is_not_sent_again(world: _World) -> None:
    ticket_id = world.ready_ticket(title="Busy", conversation_id="conv-busy")
    world.start_conversation("conv-busy")
    assert world.start_step(ticket_id).started is True
    # The turn runs on, so anything sent now queues behind it and writes no event row.
    world.record_compacted_conversation(
        "conv-busy", last_worker_step_sequence=4, compacted_through=9
    )

    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        assert readiness_loop.tell_every_worker_that_lost_its_memory() == [ticket_id]
        assert _waited_for(lambda: len(world.held_prompts("conv-busy")) == 1)
        assert readiness_loop.tell_every_worker_that_lost_its_memory() == [ticket_id]
        # The second pass finds its own notice waiting and sends nothing.
        sleep(0.2)
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()

    assert len(world.held_prompts("conv-busy")) == 1


def test_a_ticket_that_left_the_day_gets_no_notice(world: _World) -> None:
    ticket_id = world.ready_ticket(title="Off the Day", conversation_id="conv-off-day")
    world.start_conversation("conv-off-day")
    assert world.start_step(ticket_id).started is True
    world.conversations.complete_running_turn("conv-off-day")
    world.remove_from_today(ticket_id)
    world.record_compacted_conversation(
        "conv-off-day", last_worker_step_sequence=4, compacted_through=9
    )
    assert world.ticket(ticket_id).worker_step_claim is WorkerStepClaim.out

    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        assert readiness_loop.tell_every_worker_that_lost_its_memory() == []
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()

    # One write, the wake. A Ticket off the Day is not woken for any reason.
    assert len(world.conversations.backend_prompt_writes("conv-off-day")) == 1


def test_a_worker_resting_between_steps_is_left_for_its_next_wake(world: _World) -> None:
    ticket_id = world.ready_ticket(title="Resting", conversation_id="conv-resting")
    world.start_conversation("conv-resting")
    world.record_compacted_conversation(
        "conv-resting", last_worker_step_sequence=4, compacted_through=9
    )
    assert world.ticket(ticket_id).worker_step_claim is WorkerStepClaim.none

    readiness_loop, asyncio_loop, thread = _loop_in_a_thread(world)
    try:
        assert readiness_loop.tell_every_worker_that_lost_its_memory() == []
    finally:
        readiness_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()

    assert world.conversations.backend_prompt_writes("conv-resting") == ()


# --- the polling loop ----------------------------------------------------------


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
            lambda: (
                world.ticket(ready_one).conversation_id is not None
                and world.ticket(ready_two).conversation_id is not None
            )
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
            advance_ticket(
                conn, ticket_id, new_stage="needs_consequences", principal=OWNER_PRINCIPAL, now=0
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
