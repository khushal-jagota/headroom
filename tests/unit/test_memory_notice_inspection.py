"""Memory inspection exposes permission and evidence through existing read authority."""

from __future__ import annotations

import json
import sqlite3

from tests.support.principals import OWNER_PRINCIPAL

from planner.runtime import worker_memory
from planner.tickets import data, views


def _seed(conn: sqlite3.Connection) -> str:
    ticket = data.create_ticket(
        conn,
        worker_type="coding",
        title="Synthetic inspection",
        brief="Synthetic task",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    for conversation_id in ("former", "current", "other"):
        conn.execute(
            "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, "
            "access, created_at) VALUES (?, 'codex', '/synthetic', 'full', 1)",
            (conversation_id,),
        )
    for conversation_id in ("former", "current"):
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket.id),
        )
    conn.execute("UPDATE tickets SET conversation_id='current' WHERE id=?", (ticket.id,))
    for owner, conversation_id, sender in (
        (ticket.id, "former", "worker_step_memory_notice_original"),
        ("t_other", "other", "worker_step_memory_notice_other"),
    ):
        conn.execute(
            "INSERT INTO worker_memory_notice_reservations VALUES (?, ?, 7, ?, 10)",
            (owner, conversation_id, sender),
        )
        conn.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES (?, 8, 'prompt', ?, 11)",
            (conversation_id, json.dumps({"sender_message_id": sender, "text": "SECRET"})),
        )
    conn.execute(
        "INSERT INTO worker_memory_notice_holds VALUES (?, 'current', NULL, 9, "
        "'legacy_missing_event')",
        (ticket.id,),
    )
    conn.commit()
    return ticket.id


def test_detail_inspection_is_read_only_and_retains_original_associations(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket_id = _seed(tmp_db)
    before = tuple(tmp_db.iterdump())
    record_hash = data.ticket_record_sha256(tmp_db, ticket_id)
    denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
    tmp_db.set_authorizer(
        lambda action, _one, _two, _db, _source: (
            sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK
        )
    )
    detail = views.ticket_detail(tmp_db, ticket_id, 12)
    inspection = detail["memory_notice_inspection"]
    assert inspection == worker_memory.inspect_memory_notices(tmp_db, ticket_id).model_dump(
        mode="json"
    )
    assert inspection["reservations"][0]["key"]["conversation_id"] == "former"
    assert inspection["holds"][0]["context_compacted_sequence"] is None
    assert inspection["evidence"] == [
        {
            "conversation_id": "former",
            "sequence": 8,
            "kind": "prompt",
            "sender_message_id": "worker_step_memory_notice_original",
        }
    ]
    serialized = json.dumps(inspection)
    assert "SECRET" not in serialized
    assert "t_other" not in serialized
    for inferred_fact in ("delivered", "answered", "admission", "running"):
        assert inferred_fact not in serialized
    assert detail["record_sha256"] == record_hash
    assert tuple(tmp_db.iterdump()) == before
    assert not tmp_db.in_transaction
