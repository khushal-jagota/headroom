"""The decision and exact note form one durable, replay-safe instruction.

Risk gaps: prior tests cover stage resolution and temporary feedback separately, not
atomic note retention, stale client revisions, or replay identity across actors.
"""

from __future__ import annotations

from sqlite3 import Connection

import pytest
from tests.support.principals import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, ticket_principal

from planner.core.contracts import Principal
from planner.core.errors import PlannerError
from planner.tickets import data
from planner.tickets.contracts import Ticket


def _park(conn: Connection) -> Ticket:
    ticket = data.create_ticket(
        conn,
        title="Decision",
        worker_type="coding",
        brief="Brief",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        stated_ceiling="needs_what_changes",
    )
    ticket = data.file_current_proposal(
        conn, ticket.id, body="Proposed change", principal=ticket_principal(ticket.id), now=2
    )
    conn.execute("UPDATE tickets SET conversation_id='conv-existing' WHERE id=?", (ticket.id,))
    conn.commit()
    return data.read_ticket(conn, ticket.id)


@pytest.mark.parametrize("principal,actor", [(OWNER_PRINCIPAL, "User"), (CHIEF_PRINCIPAL, "Chief")])
def test_decision_exact_note_and_retry_share_one_atomic_record(
    tmp_db: Connection,
    principal: Principal,
    actor: str,
) -> None:
    ticket = _park(tmp_db)
    note = "  first line\n\nUnicode café 🧭\nlast line  "

    def accept(note_text: str = note, actor_principal: Principal = principal) -> Ticket:
        return data.accept_proposal(
            tmp_db,
            ticket.id,
            field="what_changes",
            principal=actor_principal,
            now=3,
            next_ceiling="needs_implementation",
            next_holder=OWNER_PRINCIPAL,
            message=note_text,
            expected_proposal_revision=ticket.pending_proposal_revision,
            decision_id="test-accept",
        )

    result = accept()
    assert result.stage == "needs_implementation"
    assert accept().stage == result.stage
    row = tmp_db.execute(
        "SELECT * FROM ticket_decision_messages WHERE decision_id='test-accept'"
    ).fetchone()
    assert row["message"] == note
    assert (
        row["model_text"] == f"{actor} approved the What Changes proposal with this note:\n{note}"
    )
    assert row["sender_kind"] == principal.kind.value
    assert row["proposal_revision"] == ticket.pending_proposal_revision
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM ticket_decision_messages WHERE decision_id='test-accept'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(PlannerError, match="different decision"):
        accept(note_text="changed")
    other = CHIEF_PRINCIPAL if principal == OWNER_PRINCIPAL else OWNER_PRINCIPAL
    with pytest.raises(PlannerError, match="different decision"):
        accept(actor_principal=other)


def test_stale_and_failed_actions_do_not_leave_success_notes(tmp_db: Connection) -> None:
    ticket = _park(tmp_db)
    with pytest.raises(PlannerError, match="visible proposal changed"):
        data.reject_proposal(
            tmp_db,
            ticket.id,
            principal=OWNER_PRINCIPAL,
            now=3,
            message="not for this revision",
            expected_proposal_revision=999,
            decision_id="stale",
        )
    assert (
        tmp_db.execute(
            "SELECT 1 FROM ticket_decision_messages WHERE decision_id='stale'"
        ).fetchone()
        is None
    )
    with pytest.raises(PlannerError):
        data.accept_proposal(
            tmp_db,
            ticket.id,
            field="what_changes",
            principal=OWNER_PRINCIPAL,
            now=3,
            next_ceiling="unknown-stage",
            next_holder=OWNER_PRINCIPAL,
            message="failed",
            expected_proposal_revision=ticket.pending_proposal_revision,
            decision_id="failed",
        )
    assert (
        tmp_db.execute(
            "SELECT 1 FROM ticket_decision_messages WHERE decision_id='failed'"
        ).fetchone()
        is None
    )
    assert data.read_ticket(tmp_db, ticket.id).pending_proposal == ticket.pending_proposal


def test_later_decision_and_stage_change_preserve_earlier_note(tmp_db: Connection) -> None:
    ticket = _park(tmp_db)
    data.reject_proposal(
        tmp_db,
        ticket.id,
        principal=OWNER_PRINCIPAL,
        now=3,
        message="first\ncomplete",
        expected_proposal_revision=ticket.pending_proposal_revision,
        decision_id="first",
    )
    ticket = data.file_current_proposal(
        tmp_db, ticket.id, body="New proposal", now=4, principal=ticket_principal(ticket.id)
    )
    data.accept_proposal(
        tmp_db,
        ticket.id,
        field="what_changes",
        principal=OWNER_PRINCIPAL,
        now=5,
        next_ceiling="needs_implementation",
        next_holder=OWNER_PRINCIPAL,
        message="second\ncomplete",
        expected_proposal_revision=ticket.pending_proposal_revision,
        decision_id="second",
    )
    rows = tmp_db.execute(
        "SELECT message,delivery_status FROM ticket_decision_messages "
        "WHERE decision_id IN ('first','second') ORDER BY created_at"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("first\ncomplete", "pending"),
        ("second\ncomplete", "pending"),
    ]


def test_restart_uses_actual_prompt_receipt_before_rearming_lagging_outbox(
    tmp_db: Connection,
) -> None:
    """An outbox ack can lag a real prompt; Restart must not send it a second time."""
    import json

    from planner.tickets import decision_messages

    ticket = _park(tmp_db)
    data.reject_proposal(
        tmp_db,
        ticket.id,
        principal=OWNER_PRINCIPAL,
        now=3,
        message="Already delivered",
        expected_proposal_revision=ticket.pending_proposal_revision,
        decision_id="receipt",
    )
    identity = tmp_db.execute(
        "SELECT sender_message_id FROM ticket_decision_messages WHERE decision_id='receipt'"
    ).fetchone()[0]
    tmp_db.execute(
        "INSERT INTO conversations "
        "(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES ('conv-receipt','codex','/tmp','full',0)"
    )
    tmp_db.execute(
        "INSERT INTO conversation_events (conversation_id,sequence,kind,payload,created_at) "
        "VALUES ('conv-receipt',1,'prompt',?,0)",
        (json.dumps({"sender_message_id": identity}),),
    )
    tmp_db.execute(
        "UPDATE ticket_decision_messages "
        "SET conversation_id='conv-receipt',delivery_status='queued' "
        "WHERE decision_id='receipt'"
    )
    decision_messages.rearm_after_restart(tmp_db, ticket.id)
    row = tmp_db.execute(
        "SELECT delivery_status,conversation_id FROM ticket_decision_messages "
        "WHERE decision_id='receipt'"
    ).fetchone()
    assert tuple(row) == ("delivered", "conv-receipt")
