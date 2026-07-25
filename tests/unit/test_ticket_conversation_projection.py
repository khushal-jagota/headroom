from __future__ import annotations

from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.conversation_projection import (
    TicketConversationProjection,
)


def test_projection_updates_facts_and_writes_only_on_change(tmp_path) -> None:
    db_path = str(tmp_path / "projection.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Projection",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    projection = TicketConversationProjection(db_path, now=lambda: 2)

    # Each writer answers True only when it actually changed the stored facts.
    assert projection.record_activity(ticket.id, "thinking") is True
    assert projection.record_activity(ticket.id, "thinking") is False
    assert projection.record_permission(ticket.id, True) is True
    assert projection.record_activity(ticket.id, "waiting_for_permission") is True
    waiting = projection.read(ticket.id)
    assert waiting.has_pending_permission is True
    assert projection.record_permission(ticket.id, True) is False
    assert projection.record_permission(ticket.id, False) is True
    assert projection.record_activity(ticket.id, "thinking") is True
    assert projection.record_activity(ticket.id, "idle") is True

    row = projection.read(ticket.id)
    assert row.latest_activity_state == "idle"
    assert row.has_completed_response_awaiting_user is True
    assert row.has_completed_response is True
    assert row.has_pending_permission is False
    conn.close()


def test_projection_idle_is_quiet_until_an_admitted_turn_and_preserves_attention(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "projection-idle.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Idle semantics",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    projection = TicketConversationProjection(db_path, now=lambda: 2)

    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is False
    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is False

    projection.record_activity(ticket.id, "connecting")
    projection.record_activity(ticket.id, "loading")
    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is False

    projection.record_activity(ticket.id, "thinking")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is False
    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is True

    projection.record_activity(ticket.id, "connecting")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is True
    projection.record_activity(ticket.id, "loading")
    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is True

    projection.record_activity(ticket.id, "working")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is False
    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is True
    conn.close()


def test_projection_permission_fact_only_clears_on_permission_outcome(tmp_path) -> None:
    db_path = str(tmp_path / "projection-permission.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Permission semantics",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    projection = TicketConversationProjection(db_path, now=lambda: 2)

    projection.record_permission(ticket.id, True)
    projection.record_activity(ticket.id, "waiting_for_permission")
    waiting = projection.read(ticket.id)
    assert waiting.has_pending_permission is True
    projection.record_activity(ticket.id, "thinking")
    assert projection.read(ticket.id).has_pending_permission is True
    projection.record_permission(ticket.id, False)
    resumed = projection.read(ticket.id)
    assert resumed.has_pending_permission is False
    assert resumed.has_completed_response_awaiting_user is False
    projection.record_activity(ticket.id, "idle")
    assert projection.read(ticket.id).has_completed_response_awaiting_user is True
    conn.close()


def test_acknowledgement_clears_only_completed_response_and_is_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "projection-acknowledgement.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Acknowledge response",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    projection = TicketConversationProjection(db_path, now=lambda: 2)
    projection.record_activity(ticket.id, "thinking")
    projection.record_activity(ticket.id, "idle")
    projection.record_permission(ticket.id, True)

    assert projection.acknowledge_completed_response(ticket.id) is True
    assert projection.acknowledge_completed_response(ticket.id) is False

    row = projection.read(ticket.id)
    assert row.latest_activity_state == "idle"
    assert row.has_completed_response_awaiting_user is False
    # Acknowledging clears only the awaiting bit; the reply stays remembered.
    assert row.has_completed_response is True
    assert row.has_pending_permission is True
    conn.close()


def test_has_completed_response_survives_acknowledgement_until_reset(tmp_path) -> None:
    db_path = str(tmp_path / "projection-completed-response.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Remembered reply",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    projection = TicketConversationProjection(db_path, now=lambda: 2)
    assert projection.read(ticket.id).has_completed_response is False

    # A completed turn flips both facts true together.
    projection.record_activity(ticket.id, "thinking")
    assert projection.read(ticket.id).has_completed_response is False
    projection.record_activity(ticket.id, "idle")
    completed = projection.read(ticket.id)
    assert completed.has_completed_response_awaiting_user is True
    assert completed.has_completed_response is True

    # Acknowledging clears only the awaiting bit — the seen reply stays remembered.
    projection.acknowledge_completed_response(ticket.id)
    acknowledged = projection.read(ticket.id)
    assert acknowledged.has_completed_response_awaiting_user is False
    assert acknowledged.has_completed_response is True

    # Reset deletes the row, so the memory clears.
    assert projection.reset(ticket.id) is True
    assert projection.read(ticket.id).has_completed_response is False
    conn.close()


def test_courier_flips_awaiting_approval_to_paired(tmp_path) -> None:
    db_path = str(tmp_path / "projection-courier-flip.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Courier flip",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'awaiting_approval' WHERE id = ?",
        (ticket.id,),
    )
    conn.commit()
    projection = TicketConversationProjection(db_path, now=lambda: 2)

    projection.enter_paired_on_human_prompt(ticket.id)

    row = conn.execute(
        "SELECT ticket_status FROM tickets WHERE id = ?", (ticket.id,)
    ).fetchone()
    assert str(row["ticket_status"]) == "paired"
    conn.close()


def test_courier_is_a_no_op_when_not_awaiting_approval(tmp_path) -> None:
    db_path = str(tmp_path / "projection-courier-noop.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Courier no-op",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'agent' WHERE id = ?",
        (ticket.id,),
    )
    conn.commit()
    projection = TicketConversationProjection(db_path, now=lambda: 2)

    projection.enter_paired_on_human_prompt(ticket.id)

    row = conn.execute(
        "SELECT ticket_status FROM tickets WHERE id = ?", (ticket.id,)
    ).fetchone()
    assert str(row["ticket_status"]) == "agent"
    conn.close()


def test_projection_reset_clears_stale_conversation_facts(tmp_path) -> None:
    db_path = str(tmp_path / "projection-reset.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Reset",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    projection = TicketConversationProjection(db_path, now=lambda: 2)
    projection.record_activity(ticket.id, "idle")
    projection.record_permission(ticket.id, True)
    projection.reset(ticket.id)

    row = projection.read(ticket.id)
    assert row.latest_activity_state is None
    assert row.has_completed_response_awaiting_user is False
    assert row.has_completed_response is False
    assert row.has_pending_permission is False
    conn.close()
