from __future__ import annotations

from planner.core.db import connect, create_schema
from planner.core.events import read_events_since
from planner.tickets import data as tickets_data
from planner.tickets.conversation_projection import (
    TicketConversationProjection,
)


def test_projection_updates_facts_and_emits_only_on_change(tmp_path) -> None:
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

    projection.record_activity(ticket.id, "thinking")
    projection.record_activity(ticket.id, "thinking")
    projection.record_permission(ticket.id, True)
    projection.record_activity(ticket.id, "waiting_for_permission")
    waiting = projection.read(ticket.id)
    assert waiting.has_pending_permission is True
    projection.record_permission(ticket.id, True)
    projection.record_permission(ticket.id, False)
    projection.record_activity(ticket.id, "thinking")
    projection.record_activity(ticket.id, "idle")

    row = projection.read(ticket.id)
    assert row.latest_activity_state == "idle"
    assert row.has_completed_response_awaiting_user is True
    assert row.has_pending_permission is False
    events = [
        event
        for event in read_events_since(conn, 0, 100)
        if event.kind == "ticket_conversation_projection_changed"
    ]
    assert [event.payload for event in events] == [
        {"changed": ["latest_activity_state"]},
        {"changed": ["permission"]},
        {"changed": ["latest_activity_state"]},
        {"changed": ["permission"]},
        {"changed": ["latest_activity_state"]},
        {"changed": ["latest_activity_state", "response"]},
    ]
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
    assert row.has_pending_permission is True
    projection_events = [
        event
        for event in read_events_since(conn, 0, 100)
        if event.kind == "ticket_conversation_projection_changed"
    ]
    assert projection_events[-1].payload == {"changed": ["response"]}
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
    assert row.has_pending_permission is False
    projection_events = [
        event
        for event in read_events_since(conn, 0, 100)
        if event.kind == "ticket_conversation_projection_changed"
    ]
    assert projection_events[-1].payload == {
        "changed": ["latest_activity_state", "permission"]
    }
    conn.close()
