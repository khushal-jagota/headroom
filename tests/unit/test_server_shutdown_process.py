"""What a stopping process does about worker steps that are still in flight."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from time import monotonic
from typing import cast

from planner.conversation.contracts import (
    ConversationStartRequest,
    ConversationSystem,
    PromptDeliveryFate,
    PromptDeliveryMode,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.days.logic.dates import resolve_day_id
from planner.runtime.worker_step_readiness_loop import WorkerStepReadinessLoop
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, TicketStatus
from planner.worker_context.contracts import WorkerContextService
from planner.worker_context.service import EmptyWorkerContextService


class _HoldingConversationSystem(InMemoryConversationSystem):
    """A conversation system whose send does not return until it is let go.

    It also notes whether the wait it was sitting in was cancelled, so a test can prove
    that stopping actually reached the step rather than merely walking away from it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sending = threading.Event()
        self.released = threading.Event()
        self.cancelled = threading.Event()

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
        self.sending.set()
        try:
            while not self.released.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return await super().send(
            conversation_id,
            text_message_content(text),
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
        )


def _ready_ticket(db_path: str, clock: RealClock) -> str:
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Shutdown",
            actor="human",
            now=clock.now_unix(),
            title_max_chars=200,
        )
        tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=clock.now_unix(),
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            ("conv-shutdown", ticket.id),
        )
        days_data.add_day_ticket(
            conn,
            resolve_day_id("today", clock.now(), 5),
            ticket.id,
            clock.now_unix(),
        )
    return ticket.id


def _run_event_loop_in_a_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def test_stopping_waits_out_a_worker_step_that_is_still_being_sent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "shutdown.db")
    clock = RealClock()
    ticket_id = _ready_ticket(db_path, clock)
    conversations = _HoldingConversationSystem()
    asyncio.run(
        conversations.start_conversation(
            ConversationStartRequest(conversation_id="conv-shutdown")
        )
    )
    loop, thread = _run_event_loop_in_a_thread()
    readiness_loop = WorkerStepReadinessLoop(
        db_path,
        clock,
        conversation_system=cast(ConversationSystem, conversations),
        worker_context_service=cast(WorkerContextService, EmptyWorkerContextService()),
        asyncio_loop=loop,
        boundary_hour=5,
    )
    try:
        assert readiness_loop.poll_once() == [ticket_id]
        assert conversations.sending.wait(5)
        # The step is mid-send, so the Ticket is already out at its departure status.
        with connect(db_path) as conn:
            assert tickets_data.read_ticket(conn, ticket_id).ticket_status is TicketStatus.agent

        stopping = threading.Thread(
            target=readiness_loop.stop,
            kwargs={"deadline": monotonic() + 5},
            daemon=True,
        )
        stopping.start()
        # Stopping does not return while the send is still out.
        stopping.join(0.3)
        assert stopping.is_alive()

        conversations.released.set()
        stopping.join(5)
        assert not stopping.is_alive()
        # It finished inside the deadline, so nothing cancelled it.
        assert not conversations.cancelled.is_set()
    finally:
        conversations.released.set()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()

    # The held send landed before the process let go of it, and the claim stands.
    writes = conversations.backend_prompt_writes("conv-shutdown")
    assert len(writes) == 1
    assert writes[0].sender_label == "loop"
    with connect(db_path) as conn:
        assert tickets_data.read_ticket(conn, ticket_id).ticket_status is TicketStatus.agent


def test_stopping_abandons_a_worker_step_that_outlives_the_deadline(tmp_path: Path) -> None:
    db_path = str(tmp_path / "abandon.db")
    clock = RealClock()
    ticket_id = _ready_ticket(db_path, clock)
    conversations = _HoldingConversationSystem()
    asyncio.run(
        conversations.start_conversation(
            ConversationStartRequest(conversation_id="conv-shutdown")
        )
    )
    loop, thread = _run_event_loop_in_a_thread()
    readiness_loop = WorkerStepReadinessLoop(
        db_path,
        clock,
        conversation_system=cast(ConversationSystem, conversations),
        worker_context_service=cast(WorkerContextService, EmptyWorkerContextService()),
        asyncio_loop=loop,
        boundary_hour=5,
    )
    try:
        assert readiness_loop.poll_once() == [ticket_id]
        assert conversations.sending.wait(5)
        # The deadline has already passed: stopping does not hang on the step in flight,
        # and it does not walk away leaving it running either — it cancels it, because
        # the machine lock is released the moment this returns.
        started_stopping = monotonic()
        readiness_loop.stop(deadline=monotonic() - 1)
        assert monotonic() - started_stopping < 1.0
        assert conversations.cancelled.wait(5)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()

    # The send never reached the backend, and the Ticket is left where the claim put it,
    # with no live conversation behind it. That mismatch is the honest record of a
    # process that stopped mid-step.
    assert conversations.backend_prompt_writes("conv-shutdown") == ()
    with connect(db_path) as conn:
        assert tickets_data.read_ticket(conn, ticket_id).ticket_status is TicketStatus.agent
