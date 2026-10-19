"""Transfers reach the old holder through the real conversation system."""

from __future__ import annotations

import asyncio
import sqlite3
from sqlite3 import Connection

import pytest
from tests.unit.test_conversation_system import _Harness
from tests.unit.test_proposal_holder import _outcome, _park, _park_under

from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.message_content import message_content_text, text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.membership.api import add_member, remove_member
from planner.runtime import conversation_start
from planner.tickets import data
from planner.tickets.api import patch_ticket
from planner.tickets.contracts import ReviewTransferNotice, TicketEdit
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


def _system(conn: Connection) -> _Harness:
    return _Harness(conn.execute("PRAGMA database_list").fetchone()[2])


def test_patch_transfer_queues_notice_to_busy_chief_without_reply_request(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: Clock,
) -> None:
    ticket = _park(tmp_db, CHIEF_PRINCIPAL)
    harness = _system(tmp_db)

    async def exercise() -> None:
        try:
            # The existing conversation is busy, so the notice must wait its turn.
            linked = await conversation_start.start_agent_conversation(
                harness.system,
                tmp_db,
                CHIEF_SETTINGS_KEY,
                conversation_start.agent_resolve(tmp_db),
                conversation_id="chief-review",
            )
            await harness.system.send(
                linked.conversation_id, text_message_content("Existing work"), sender_label="Panels"
            )
            await patch_ticket(
                ticket.id,
                {"ceiling_holder": {"kind": "owner", "id": "owner"}},
                tmp_db,
                RequestContext(OWNER_PRINCIPAL),
                cfg,
                fake_clock,
                harness.system,
            )
            assert len(harness.backend("chief-review").writes) == 1
            await harness.complete_turn("chief-review")
            assert message_content_text(
                harness.backend("chief-review").writes[-1].sender_content or ()
            ) == (f"The user took over the review of {ticket.title}.")
            turn = await harness.system.active_turn_reference("chief-review")
            assert turn is not None
            assert not await harness.system.turn_expects_reply(turn, OWNER_PRINCIPAL)
        finally:
            await harness.system.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("new_holder", ["chief", "sprint_item"])
def test_owner_notice_creates_ticket_transcript_without_worker_prompt(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: Clock,
    new_holder: str,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    item_id = _outcome(tmp_db, "Review desk", fake_clock)
    holder_id = "chief" if new_holder == "chief" else item_id
    holder_name = "The Chief" if new_holder == "chief" else "Review desk"
    harness = _system(tmp_db)

    async def exercise() -> None:
        try:
            await patch_ticket(
                ticket.id,
                {
                    "orchestrator": {"kind": "sprint_item", "id": item_id},
                    "ceiling_holder": {"kind": new_holder, "id": holder_id},
                },
                tmp_db,
                RequestContext(OWNER_PRINCIPAL),
                cfg,
                fake_clock,
                harness.system,
            )
            current = data.read_ticket(tmp_db, ticket.id).conversation_id
            assert current is not None
            rows = await harness.events(current)
            owner_messages = [
                row.payload for row in rows if isinstance(row.payload, MessageToOwnerEventPayload)
            ]
            assert len(owner_messages) == 1
            assert message_content_text(owner_messages[0].content) == (
                f"{holder_name} took over the review of {ticket.title}."
            )
            assert owner_messages[0].sender_label == "Panels"
            assert harness.spawned_conversation_ids == []
        finally:
            await harness.system.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("route", ["patch", "add", "remove"])
def test_assignment_transfer_reaches_previous_orchestrator(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: Clock,
    route: str,
) -> None:
    left = _outcome(tmp_db, "Inbox triage", fake_clock)
    joined = _outcome(tmp_db, "Sprint hygiene", fake_clock)
    ticket = _park_under(tmp_db, left)
    harness = _system(tmp_db)

    async def exercise() -> None:
        try:
            if route == "patch":
                await patch_ticket(
                    ticket.id,
                    {"orchestrator": {"kind": "sprint_item", "id": joined}},
                    tmp_db,
                    RequestContext(OWNER_PRINCIPAL),
                    cfg,
                    fake_clock,
                    harness.system,
                )
            else:
                action = add_member if route == "add" else remove_member
                await action(
                    "outcome_tickets",
                    joined if route == "add" else left,
                    ticket.id,
                    tmp_db,
                    RequestContext(OWNER_PRINCIPAL),
                    cfg,
                    fake_clock,
                    harness.system,
                )
            key = f"sprint_item_supervisor_{left}"
            current = conversation_start.read_agent_conversation(tmp_db, key)
            assert current is not None
            assert message_content_text(
                harness.backend(current).writes[-1].sender_content or ()
            ) == (f"The user took over the review of {ticket.title}.")
            assert (
                "[Authenticated Panels reply requirement]"
                not in harness.backend(current).writes[-1].text
            )
            assert data.read_ticket(tmp_db, ticket.id).ceiling_holder == OWNER_PRINCIPAL
        finally:
            await harness.system.shutdown()

    asyncio.run(exercise())


def test_final_holder_and_commit_determine_notice_not_intermediate_edit(
    tmp_db: Connection,
    fake_clock: Clock,
) -> None:
    left = _outcome(tmp_db, "Inbox triage", fake_clock)
    joined = _outcome(tmp_db, "Sprint hygiene", fake_clock)
    ticket = _park_under(tmp_db, left)
    notices: list[ReviewTransferNotice] = []
    # Owner -> temporarily invalid item -> automatic owner return is no transfer.
    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=20,
    )
    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(
            sprint_item_id=joined, ceiling_holder=Principal(PrincipalKind.sprint_item, left)
        ),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=21,
        review_transfer_notices=notices,
    )
    assert notices == []
    # A savepoint release cannot publish a notice before the outer commit.
    tmp_db.execute("BEGIN IMMEDIATE")
    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(ceiling_holder=CHIEF_PRINCIPAL),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=22,
        review_transfer_notices=notices,
    )
    tmp_db.rollback()
    assert notices == []
    assert data.read_ticket(tmp_db, ticket.id).ceiling_holder == OWNER_PRINCIPAL

    # A failed COMMIT also leaves no publishable notice.
    def refuse_commit(
        action: int,
        operation: str | None,
        _column: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    tmp_db.set_authorizer(refuse_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            data.edit_ticket(
                tmp_db,
                ticket.id,
                edit=TicketEdit(ceiling_holder=CHIEF_PRINCIPAL),
                title_max_chars=200,
                principal=OWNER_PRINCIPAL,
                now=23,
                review_transfer_notices=notices,
            )
    finally:
        tmp_db.set_authorizer(None)
        tmp_db.rollback()
    assert notices == []
    assert data.read_ticket(tmp_db, ticket.id).ceiling_holder == OWNER_PRINCIPAL
    # No parked proposal means there is no review to take away.
    empty = data.create_ticket(
        tmp_db,
        title="No review",
        principal=OWNER_PRINCIPAL,
        now=23,
        title_max_chars=200,
        worker_type="coding",
        brief=None,
    )
    data.edit_ticket(
        tmp_db,
        empty.id,
        edit=TicketEdit(ceiling_holder=CHIEF_PRINCIPAL),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=24,
        review_transfer_notices=notices,
    )
    assert notices == []


def test_refused_notice_does_not_undo_committed_transfer(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ticket = _park(tmp_db, CHIEF_PRINCIPAL)
    harness = _system(tmp_db)

    async def exercise() -> None:
        try:
            await conversation_start.start_agent_conversation(
                harness.system,
                tmp_db,
                CHIEF_SETTINGS_KEY,
                conversation_start.agent_resolve(tmp_db),
                conversation_id="failed-chief",
            )
            harness.backend("failed-chief").spawn_fails = True
            result = await patch_ticket(
                ticket.id,
                {"ceiling_holder": {"kind": "owner", "id": "owner"}},
                tmp_db,
                RequestContext(OWNER_PRINCIPAL),
                cfg,
                fake_clock,
                harness.system,
            )
            assert result["ceiling_holder"] == {"kind": "owner", "id": "owner"}
            assert data.read_ticket(tmp_db, ticket.id).ceiling_holder == OWNER_PRINCIPAL
            assert "review transfer notice did not reach its holder" in caplog.text
            caplog.clear()
            await patch_ticket(
                ticket.id,
                {"ceiling_holder": {"kind": "owner", "id": "owner"}},
                tmp_db,
                RequestContext(OWNER_PRINCIPAL),
                cfg,
                fake_clock,
                harness.system,
            )
            assert caplog.text == ""
            assert harness.backend("failed-chief").session_starts == 0
        finally:
            await harness.system.shutdown()

    asyncio.run(exercise())
