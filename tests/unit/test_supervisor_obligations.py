"""Durability rules for Sprint Item supervisor obligations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from planner.core.clock import TestClock
from planner.core.contracts import Priority
from planner.core.db import connect, create_schema
from planner.sprints import data as sprints_data
from planner.supervisor_obligations import data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TicketStatus


def _database(tmp_path: Path):
    path = tmp_path / "panels.db"
    conn = connect(str(path))
    create_schema(conn)
    return conn


def _item(conn):
    return sprints_data.create_item(
        conn,
        title="Item",
        body="",
        priority=Priority.P3,
        deadline=None,
        project_id="project_vylo",
        sprint_id=None,
        clock=TestClock(datetime.fromtimestamp(1, UTC)),
    )


def _ticket(conn, item_id: str, title: str = "Ticket", now: int = 2):
    return tickets_data.create_ticket(
        conn,
        title=title,
        worker_type="coding",
        kickoff_note="Start",
        project_id="project_vylo",
        sprint_item_id=item_id,
        now=now,
        actor="direct",
        title_max_chars=200,
    )


def test_status_projection_is_idempotent_and_ack_does_not_resolve(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    item = _item(conn)
    ticket = _ticket(conn, item.id)
    tickets_data._write_ticket_status(conn, ticket.id, TicketStatus.needs_user, 3)
    data.reconcile(conn, 4)
    obligations = data.list_for_item(conn, item.id)
    assert len(obligations) == 1
    assert obligations[0].kind.value == "needs_user"
    assert data.acknowledge(conn, item.id, [obligations[0].id], 5) == 1
    data.reconcile(conn, 6)
    assert data.list_for_item(conn, item.id)[0].lifecycle.value == "acknowledged"
    tickets_data._write_ticket_status(conn, ticket.id, TicketStatus.empty, 7)
    data.reconcile(conn, 8)
    assert data.list_for_item(conn, item.id) == ()


def test_batch_membership_is_ordered_and_recoverable(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    item = _item(conn)
    for number in (1, 2):
        ticket = _ticket(conn, item.id, f"Ticket {number}", number + 1)
        tickets_data._write_ticket_status(conn, ticket.id, TicketStatus.needs_user, 10 + number)
    claimed = data.claim_batch(conn, 20)
    assert claimed is not None
    delivery, obligations = claimed
    assert len(obligations) == 2
    prepared = data.prepared_delivery(conn)
    assert prepared is not None
    assert prepared[0] == delivery
    assert [entry.id for entry in prepared[1]] == [entry.id for entry in obligations]
    data.settle_delivery(conn, delivery.id, state="queued", now=21, conversation_id="conv")
    assert all(
        obligation.lifecycle.value == "pending" for obligation in data.list_for_item(conn, item.id)
    )
    data.mark_queued_outcome_uncertain(conn, delivery.id, 22)
    assert data.prepared_delivery(conn) is None
    assert data.list_for_item(conn, item.id)[0].lifecycle.value == "failed"


def test_item_deletion_cascades_obligations(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    item = _item(conn)
    ticket = _ticket(conn, item.id)
    tickets_data._write_ticket_status(conn, ticket.id, TicketStatus.needs_user, 3)
    tickets_data.delete_ticket(conn, ticket.id, actor="human", now=4)
    conn.execute("BEGIN IMMEDIATE")
    sprints_data._delete_item_rows(conn, item.id)
    conn.execute("COMMIT")
    assert conn.execute("SELECT count(*) FROM supervisor_obligations").fetchone()[0] == 0
