from __future__ import annotations

from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.tickets import data as tickets_data
from planner.tickets import views as ticket_views
from planner.tickets.contracts import Ticket


@pytest.fixture
def ticket(tmp_db: Connection) -> Ticket:
    return tickets_data.create_ticket(
        tmp_db,
        title="History",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )


def _conversation(conn: Connection, conversation_id: str, created_at: int) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES (?, 'codex', 'model', '/work', 'full', ?)",
        (conversation_id, created_at),
    )


def test_detail_orders_history_and_owner_lookup_resolves_past_conversations(
    tmp_db: Connection, ticket: Ticket
) -> None:
    for conversation_id, created_at in (
        ("conv_z", 20),
        ("conv_b", 10),
        ("conv_a", 10),
    ):
        _conversation(tmp_db, conversation_id, created_at)
        tmp_db.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket.id),
        )
    tmp_db.execute("UPDATE tickets SET conversation_id = 'conv_z' WHERE id = ?", (ticket.id,))

    detail = ticket_views.ticket_detail(tmp_db, ticket.id, 30)

    assert detail["conversation_id"] == "conv_z"
    assert detail["conversation_history"] == [
        {"conversation_id": "conv_a", "created_at": 10},
        {"conversation_id": "conv_b", "created_at": 10},
        {"conversation_id": "conv_z", "created_at": 20},
    ]
    assert tickets_data.read_ticket_by_conversation_id(tmp_db, "conv_a").id == ticket.id
