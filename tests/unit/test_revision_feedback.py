from __future__ import annotations

from sqlite3 import Connection

from tests.support.principals import OWNER_PRINCIPAL, TEST_TICKET_PRINCIPAL

from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback
from planner.tickets.contracts import NO_FURTHER, Ticket


def test_revision_feedback_is_attributed_stage_scoped_and_acknowledged_by_revision(
    tmp_db: Connection,
) -> None:
    ticket = _ticket(tmp_db)
    first = revision_feedback.set_feedback(
        tmp_db,
        ticket.id,
        stage=ticket.stage,
        sender=OWNER_PRINCIPAL,
        message="first feedback",
        now=2,
    )
    second = revision_feedback.set_feedback(
        tmp_db,
        ticket.id,
        stage=ticket.stage,
        sender=TEST_TICKET_PRINCIPAL,
        message="  exact replacement\nwith spacing  ",
        now=3,
    )

    assert second.revision == first.revision + 1
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM ticket_revision_feedback WHERE ticket_id=?", (ticket.id,)
        ).fetchone()[0]
        == 1
    )
    snapshot = revision_feedback.snapshot(tmp_db, ticket.id)
    assert snapshot is not None
    assert "first feedback" not in snapshot.text
    assert "ticket t_test" in snapshot.text
    assert "  exact replacement\nwith spacing  " in snapshot.text

    revision_feedback.acknowledge(tmp_db, ticket.id, first.revision)
    assert revision_feedback.snapshot(tmp_db, ticket.id) == snapshot
    revision_feedback.acknowledge(tmp_db, ticket.id, second.revision)
    assert revision_feedback.snapshot(tmp_db, ticket.id) is None


def _ticket(tmp_db: Connection) -> Ticket:
    ticket = tickets_data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Context ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    return tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=1,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
