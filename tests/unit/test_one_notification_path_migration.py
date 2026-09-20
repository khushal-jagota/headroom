"""The drop leaves the attention state, its edges, and a log that still says what was sent."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema
from planner.notifications import data as notifications_data

PREVIOUS_REVISION = "derive_ticket_status"


def _upgrade_to(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), revision)
    engine.dispose()


def _database_with_both_paths_behind_it(tmp_path: Path) -> Path:
    """One delivery from an attention edge, and one from the legacy fact path."""
    db_path = tmp_path / "one-notification-path.db"
    _upgrade_to(db_path, PREVIOUS_REVISION)
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) "
        "VALUES ('project_example', 'Example', 1, 1)"
    )
    conn.execute(
        "INSERT INTO tickets(id, title, worker_type, employee_backend, stage, ceiling, "
        "project_id, field_values, created_at, updated_at) "
        "VALUES ('t_existing', 'Existing Ticket', 'coding', 'claude', 'needs_success', "
        "'needs_success', 'project_example', '{}', 1, 1)"
    )
    conn.execute(
        "INSERT INTO notification_push_subscriptions"
        "(subscription_id, endpoint, p256dh, auth, created_at, updated_at) "
        "VALUES ('sub_phone', 'https://push.example/phone', 'key', 'auth', 1, 1)"
    )
    conn.execute(
        "INSERT INTO notification_attention_state"
        "(subject_kind, subject_id, notification_type, active, generation) "
        "VALUES ('ticket', 't_existing', 'awaiting_approval', 1, 1)"
    )
    conn.execute(
        "INSERT INTO notification_attention_edges"
        "(subject_kind, subject_id, notification_type, generation, occurred_at, projected) "
        "VALUES ('ticket', 't_existing', 'awaiting_approval', 1, 9, 1)"
    )
    for fact_id, occurred_at in (
        ("attention:ticket:t_existing:awaiting_approval:1", 9),
        ("ticket:t_existing:4", 5),
    ):
        conn.execute(
            "INSERT INTO notification_facts"
            "(fact_id, notification_type, subject_kind, ticket_id, source_kind, source_id, "
            "source_sequence, occurred_at, payload) "
            "VALUES (?, 'awaiting_approval', 'ticket', 't_existing', 'ticket', 't_existing', "
            "1, ?, '{\"subject_label\":\"Existing Ticket\"}')",
            (fact_id, occurred_at),
        )
        conn.execute(
            "INSERT INTO notification_decisions(fact_id, outcome, decided_at) "
            "VALUES (?, 'notify', ?)",
            (fact_id, occurred_at),
        )
        conn.execute(
            "INSERT INTO notification_intents"
            "(fact_id, title, body, route, tag, created_at) "
            "VALUES (?, 'Panels', 'Existing Ticket needs your approval.', "
            "'/#/workspace/t_existing', 'panels-ticket-t_existing', ?)",
            (fact_id, occurred_at),
        )
        conn.execute(
            "INSERT INTO notification_deliveries"
            "(fact_id, subscription_id, status, attempts, next_attempt_at, delivered_at) "
            "VALUES (?, 'sub_phone', 'delivered', 1, ?, ?)",
            (fact_id, occurred_at, occurred_at),
        )
    conn.close()
    return db_path


def test_upgrade_keeps_the_edge_delivery_and_drops_the_legacy_one(tmp_path: Path) -> None:
    db_path = _database_with_both_paths_behind_it(tmp_path)
    with connect(str(db_path)) as before:
        assert before.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 2

    conn = connect(str(db_path))
    create_schema(conn)
    # The edge-derived row keeps its history, including what it said and how it went.
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT subject_kind, subject_id, notification_type, generation, subscription_id, "
            "title, body, route, tag, created_at, status, attempts, delivered_at "
            "FROM notification_deliveries"
        )
    ] == [
        (
            "ticket",
            "t_existing",
            "awaiting_approval",
            1,
            "sub_phone",
            "Panels",
            "Existing Ticket needs your approval.",
            "/#/workspace/t_existing",
            "panels-ticket-t_existing",
            9,
            "delivered",
            1,
            9,
        )
    ]
    # The four stages are gone from the schema, not merely unused.
    remaining = {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'notification%'"
        )
    }
    assert remaining == {
        "notification_attention_state",
        "notification_attention_edges",
        "notification_deliveries",
        "notification_preferences",
        "notification_push_subscriptions",
        "notification_web_push_identity",
    }
    # The edge flag now records the whole decision.
    assert [str(row[1]) for row in conn.execute("PRAGMA table_info(notification_attention_edges)")][
        -1
    ] == "decided"
    assert (
        conn.execute("SELECT decided FROM notification_attention_edges").fetchone()["decided"] == 1
    )
    # And the loop's real move runs over the upgraded database without repeating itself.
    assert notifications_data.queue_deliveries(conn, 20) == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
