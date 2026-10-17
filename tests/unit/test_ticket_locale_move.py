"""Locale moves preserve the Ticket and cannot cross message admission."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal

from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import MessageContent, text_message_content
from planner.core.errors import PlannerError
from planner.runtime.conversation_start import conversation_link_lock, send_to_ticket_conversation
from planner.runtime.worker_step_readiness import worker_step_blocker
from planner.tickets import data, locale_configuration
from planner.tickets.contracts import Ticket, TicketLocale
from planner.tickets.locale_move import move_ticket_locale
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def ticket(tmp_db: Connection, monkeypatch: pytest.MonkeyPatch) -> Ticket:
    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        frozenset({"codex", "claude", "hermes"}),
    )
    return data.create_ticket(
        tmp_db, title="A movable ticket", principal=OWNER_PRINCIPAL,
        now=1, title_max_chars=200, worker_type="coding",
    )


def test_move_changes_only_locale(tmp_db: Connection, ticket: Ticket) -> None:
    async def exercise() -> None:
        moved = await move_ticket_locale(
            InMemoryConversationSystem(), tmp_db, ticket.id,
            locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=20,
        )
        assert moved == replace(ticket, locale=TicketLocale.macos)
    asyncio.run(exercise())


@pytest.mark.parametrize("claim", ["out", "errored"])
def test_move_refuses_outstanding_worker_claim(
    tmp_db: Connection, ticket: Ticket, claim: str,
) -> None:
    tmp_db.execute("UPDATE tickets SET worker_step_claim=? WHERE id=?", (claim, ticket.id))
    tmp_db.commit()
    with pytest.raises(PlannerError, match="outstanding worker-step claim"):
        asyncio.run(move_ticket_locale(
            InMemoryConversationSystem(), tmp_db, ticket.id,
            locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=20,
        ))
    assert data.read_ticket(tmp_db, ticket.id).locale is TicketLocale.vps


@pytest.mark.parametrize("held", [False, True])
def test_move_refuses_running_or_held_messages(
    tmp_db: Connection, ticket: Ticket, held: bool,
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await system.start_conversation(ConversationStartRequest(conversation_id="conv", model="m"))
        tmp_db.execute("UPDATE tickets SET conversation_id='conv' WHERE id=?", (ticket.id,))
        tmp_db.commit()
        await system.send("conv", text_message_content("work"), sender_label="Owner")
        if held:
            await system.send("conv", text_message_content("waiting"), sender_label="Owner")
            # Isolate held-only activity, without the running turn also refusing.
            system._conversations["conv"].running_turn = None
        with pytest.raises(PlannerError, match="activity or waiting messages"):
            await move_ticket_locale(
                system, tmp_db, ticket.id, locale=TicketLocale.macos,
                principal=OWNER_PRINCIPAL, now=20,
            )
        assert data.read_ticket(tmp_db, ticket.id).locale is TicketLocale.vps
    asyncio.run(exercise())


def test_move_wins_before_send_and_start_is_refused(tmp_db: Connection, ticket: Ticket) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await move_ticket_locale(
            system, tmp_db, ticket.id, locale=TicketLocale.macos,
            principal=OWNER_PRINCIPAL, now=20,
        )
        async with conversation_link_lock(f"ticket:{ticket.id}"):
            with pytest.raises(PlannerError, match="cannot start on this vps machine"):
                await send_to_ticket_conversation(
                    system, tmp_db, ticket.id, text_message_content("start"),
                    conversation_id=None, sender_label="Owner", now=21,
                )
        assert data.read_ticket(tmp_db, ticket.id).conversation_id is None
    asyncio.run(exercise())


def test_send_wins_before_move_and_move_is_refused(tmp_db: Connection, ticket: Ticket) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await system.start_conversation(ConversationStartRequest(conversation_id="conv", model="m"))
        tmp_db.execute("UPDATE tickets SET conversation_id='conv' WHERE id=?", (ticket.id,))
        tmp_db.commit()
        content_started = asyncio.Event()
        release_content = asyncio.Event()

        async def content(conversation_id: str) -> MessageContent:
            content_started.set()
            await release_content.wait()
            return text_message_content("start")

        async def send() -> None:
            async with conversation_link_lock(f"ticket:{ticket.id}"):
                await send_to_ticket_conversation(
                    system, tmp_db, ticket.id, content,
                    conversation_id="conv", sender_label="Owner", now=21,
                )

        sending = asyncio.create_task(send())
        await content_started.wait()
        moving = asyncio.create_task(move_ticket_locale(
            system, tmp_db, ticket.id, locale=TicketLocale.macos,
            principal=OWNER_PRINCIPAL, now=22,
        ))
        with pytest.raises(PlannerError, match="conversation operation is in progress"):
            await asyncio.wait_for(moving, timeout=1)
        release_content.set()
        await sending
        assert data.read_ticket(tmp_db, ticket.id).locale is TicketLocale.vps
    asyncio.run(exercise())


def test_wrong_machine_readiness_is_after_backlog(tmp_db: Connection, ticket: Ticket) -> None:
    tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (ticket.id,))
    tmp_db.commit()
    definition = configured_worker_type_registry().require(ticket.worker_type)
    remote = data.read_ticket(tmp_db, ticket.id)
    assert worker_step_blocker(tmp_db, remote, worker_type_definition=definition) == (
        "the Ticket locale is macos, so it cannot start on this vps machine"
    )
    tmp_db.execute("UPDATE tickets SET is_backlogged=1 WHERE id=?", (ticket.id,))
    tmp_db.commit()
    assert worker_step_blocker(
        tmp_db, data.read_ticket(tmp_db, ticket.id), worker_type_definition=definition,
    ) == "the Ticket is backlogged"


def test_worker_cannot_move_itself(tmp_db: Connection, ticket: Ticket) -> None:
    with pytest.raises(PlannerError):
        asyncio.run(move_ticket_locale(
            InMemoryConversationSystem(), tmp_db, ticket.id,
            locale=TicketLocale.macos, principal=ticket_principal(ticket.id), now=20,
        ))
    assert data.read_ticket(tmp_db, ticket.id).locale is TicketLocale.vps




def test_idle_linked_conversation_can_move_without_resetting_it(
    tmp_db: Connection, ticket: Ticket,
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await system.start_conversation(ConversationStartRequest(conversation_id="conv", model="m"))
        tmp_db.execute("UPDATE tickets SET conversation_id='conv' WHERE id=?", (ticket.id,))
        tmp_db.commit()
        before = data.read_ticket(tmp_db, ticket.id)
        moved = await move_ticket_locale(
            system, tmp_db, ticket.id, locale=TicketLocale.macos,
            principal=OWNER_PRINCIPAL, now=20,
        )
        assert moved == replace(before, locale=TicketLocale.macos)
        assert system.backend_prompt_writes("conv") == ()
    asyncio.run(exercise())


@pytest.mark.parametrize("first", ["claim", "move"])
def test_separate_connections_cannot_claim_and_move_the_same_ticket(
    tmp_db: Connection, ticket: Ticket, first: str,
) -> None:
    from tests.support.ticket_progress import advance_ticket

    from planner.core.db import connect
    from planner.runtime.worker_step_readiness import is_ready_for_worker_step
    from planner.tickets.contracts import WorkerStepClaim

    advance_ticket(
        tmp_db, ticket.id, new_stage="needs_success_condition",
        principal=OWNER_PRINCIPAL, now=2,
    )
    database_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    other = connect(database_path)
    try:
        async def move() -> Ticket:
            return await move_ticket_locale(
                InMemoryConversationSystem(), other, ticket.id,
                locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=4,
            )

        def claim() -> Ticket | None:
            return data.claim_ticket_for_worker_step(
                tmp_db, ticket.id, readiness_check=is_ready_for_worker_step, now=3,
            )

        if first == "claim":
            assert claim() is not None
            with pytest.raises(PlannerError, match="outstanding worker-step claim"):
                asyncio.run(move())
            final = data.read_ticket(other, ticket.id)
            assert final.locale is TicketLocale.vps
            assert final.worker_step_claim is WorkerStepClaim.out
        else:
            assert asyncio.run(move()).locale is TicketLocale.macos
            assert claim() is None
            final = data.read_ticket(other, ticket.id)
            assert final.locale is TicketLocale.macos
            assert final.worker_step_claim is WorkerStepClaim.none
    finally:
        other.close()


@pytest.mark.parametrize("first", ["claim", "move"])
def test_concurrent_claim_and_move_serialize_their_database_writes(
    tmp_db: Connection, ticket: Ticket, first: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from tests.support.ticket_progress import advance_ticket

    from planner.core.db import connect
    from planner.runtime.worker_step_readiness import is_ready_for_worker_step
    from planner.tickets.contracts import WorkerStepClaim
    from planner.worker_types.contracts import WorkerTypeDefinition

    advance_ticket(
        tmp_db, ticket.id, new_stage="needs_success_condition",
        principal=OWNER_PRINCIPAL, now=2,
    )
    database_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    first_has_write_lock = Event()
    release_first = Event()
    contender_attempted_begin = Event()
    original_compatibility = locale_configuration.require_locale_backend_compatibility

    def hold_first_write() -> None:
        first_has_write_lock.set()
        assert release_first.wait(5), "the test did not release the first writer"

    def compatibility(locale: TicketLocale, backend: str) -> None:
        original_compatibility(locale, backend)
        if first == "move":
            hold_first_write()

    monkeypatch.setattr(data, "require_locale_backend_compatibility", compatibility)

    def readiness(
        conn: Connection, ticket: Ticket, *, worker_type_definition: WorkerTypeDefinition,
    ) -> bool:
        ready = is_ready_for_worker_step(
            conn, ticket, worker_type_definition=worker_type_definition,
        )
        if first == "claim":
            hold_first_write()
        return ready

    def run_operation(operation: str, *, contender: bool = False) -> Ticket | None:
        conn = connect(database_path)
        if contender:
            def trace(statement: str) -> None:
                if statement == "BEGIN IMMEDIATE":
                    contender_attempted_begin.set()
            conn.set_trace_callback(trace)
        try:
            if operation == "claim":
                return data.claim_ticket_for_worker_step(
                    conn, ticket.id, readiness_check=readiness, now=3,
                )
            return asyncio.run(move_ticket_locale(
                InMemoryConversationSystem(), conn, ticket.id,
                locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=4,
            ))
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        leading = executor.submit(run_operation, first)
        try:
            assert first_has_write_lock.wait(5)
            following = executor.submit(
                run_operation, "move" if first == "claim" else "claim", contender=True,
            )
            assert contender_attempted_begin.wait(5)
            assert not following.done()
        finally:
            release_first.set()
        assert leading.result(timeout=5) is not None
        if first == "claim":
            with pytest.raises(PlannerError, match="outstanding worker-step claim"):
                following.result(timeout=5)
        else:
            assert following.result(timeout=5) is None

    final = data.read_ticket(tmp_db, ticket.id)
    assert (final.locale, final.worker_step_claim) == (
        (TicketLocale.vps, WorkerStepClaim.out) if first == "claim"
        else (TicketLocale.macos, WorkerStepClaim.none)
    )
