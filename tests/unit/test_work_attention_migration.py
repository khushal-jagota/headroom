from __future__ import annotations

import time
from pathlib import Path

from alembic import command
from tests.support.historical_tickets import insert_historical_ticket

from planner.core import db as db_module
from planner.core.db import connect, create_schema
from planner.notifications import data as notifications_data


def _upgrade_to(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), revision)
    finally:
        engine.dispose()


def test_upgrade_seeds_attention_and_acknowledges_only_stale_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "work-attention.db"
    _upgrade_to(db_path, "durable_rejection_messages")
    conn = connect(str(db_path))
    now = int(time.time())
    old = now - 90_000
    recent = now - 100
    tickets = [
        insert_historical_ticket(
            conn,
            ticket_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
        )
        for ticket_id, title, created_at in (
            ("t_help", "Legacy help", old),
            ("t_stale", "Stale error", old),
            ("t_recent", "Recent error", recent),
        )
    ]
    for ticket, conversation_id, created_at in (
        (tickets[0], "c_help", old),
        (tickets[1], "c_stale", old),
        (tickets[2], "c_recent", recent),
    ):
        conn.execute(
            "INSERT INTO conversations"
            "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
            "VALUES (?, 'codex', '/tmp/workspace', 'full', ?, ?)",
            (conversation_id, 0 if conversation_id == "c_help" else 1, created_at),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (conversation_id, ticket),
        )
    for conversation_id, created_at in (("c_stale", old), ("c_recent", recent)):
        conn.execute(
            "INSERT INTO conversation_events"
            "(conversation_id, sequence, kind, payload, created_at) "
            "VALUES (?, 1, 'turn_ended', '{\"ending\":\"failed\"}', ?)",
            (conversation_id, created_at),
        )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'needs_user', ticket_status_changed_at = ? "
        "WHERE id = ?",
        (old, tickets[0]),
    )
    for ticket, changed_at in ((tickets[1], old), (tickets[2], recent)):
        conn.execute(
            "UPDATE tickets SET ticket_status = 'errored', backend_error = 'failed', "
            "ticket_status_changed_at = ? WHERE id = ?",
            (changed_at, ticket),
        )
    conn.close()

    # The cursor this revision advances lives until one_notification_path drops it, so
    # that part of the proof runs at the last revision that still holds it.
    _upgrade_to(db_path, "derive_ticket_status")
    with connect(str(db_path)) as before_the_drop:
        help_cursor = before_the_drop.execute(
            "SELECT sequence FROM notification_projection_cursors "
            "WHERE source_kind = 'conversation' AND source_id = 'c_help'"
        ).fetchone()
        assert help_cursor is not None and int(help_cursor["sequence"]) == 1
        assert before_the_drop.execute(
            "SELECT COUNT(*) FROM notification_facts"
        ).fetchone()[0] == 0

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert upgraded.execute(
        "SELECT COUNT(*) FROM conversation_events "
        "WHERE conversation_id = 'c_help' AND kind = 'message_to_owner'"
    ).fetchone()[0] == 1
    # A seeded state with no edge behind it is not a notification.
    assert notifications_data.queue_deliveries(upgraded, int(time.time())) == 0
    assert upgraded.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 0

    claims = {
        str(row["title"]): str(row["worker_step_claim"])
        for row in upgraded.execute(
            "SELECT title, worker_step_claim FROM tickets WHERE id IN (?, ?)",
            (tickets[1], tickets[2]),
        )
    }
    assert claims == {"Stale error": "none", "Recent error": "errored"}
    assert upgraded.execute(
        "SELECT through_sequence FROM conversation_error_acknowledgements "
        "WHERE conversation_id = 'c_stale'"
    ).fetchone()[0] == 1
    assert upgraded.execute(
        "SELECT 1 FROM conversation_error_acknowledgements "
        "WHERE conversation_id = 'c_recent'"
    ).fetchone() is None
    upgraded.close()
