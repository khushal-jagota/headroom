"""The Ticket-to-typed-subject notification migration preserves durable history."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema
from planner.notifications import data as notifications_data

PREVIOUS_REVISION = "notifications"
HEAD_REVISION = "project_folder_path"


def _upgrade_to_previous_revision(path: Path) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    engine.dispose()


def test_upgrade_preserves_delivery_graph_and_seeds_agent_no_history_cursor(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "notification-subjects.db"
    _upgrade_to_previous_revision(db_path)
    conn = connect(str(db_path))
    ticket_id = "t_existing"
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, fields, "
        "created_at, updated_at) VALUES "
        "(?, 'Existing Ticket', 'coding', 'hermes', 'needs_kickoff', "
        "'needs_success', '{}', 1, 1)",
        (ticket_id,),
    )
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_existing_chief', 'codex', '/tmp/workspace', 'full', 1, 1)"
    )
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_existing_chief', 1, 'turn_ended', "
        '\'{"ending":"completed","error_summary":null}\', 2)'
    )
    conn.execute(
        "INSERT INTO agents(agent_key, conversation_id) "
        "VALUES ('chief_of_staff', 'c_existing_chief')"
    )
    conn.execute(
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, ticket_id, source_kind, source_id, "
        "source_sequence, occurred_at, payload) "
        "VALUES ('old:decided', 'worker_completed', ?, 'ticket', ?, 1, 2, "
        '\'{"ticket_title":"Existing Ticket"}\')',
        (ticket_id, ticket_id),
    )
    conn.execute(
        "INSERT INTO notification_decisions(fact_id, outcome, decided_at) "
        "VALUES ('old:decided', 'notify', 3)"
    )
    conn.execute(
        "INSERT INTO notification_intents"
        "(fact_id, title, body, route, tag, created_at) "
        "VALUES ('old:decided', 'Panels', 'Existing Ticket completed.', "
        "'/#/ticket/existing', 'panels-ticket-existing', 3)"
    )
    conn.execute(
        "INSERT INTO notification_push_subscriptions"
        "(subscription_id, endpoint, p256dh, auth, created_at, updated_at, disabled_at) "
        "VALUES ('sub_existing', 'https://push.example/existing', 'key', 'auth', 1, 1, NULL)"
    )
    conn.execute(
        "INSERT INTO notification_deliveries"
        "(fact_id, subscription_id, status, attempts, next_attempt_at, "
        "last_error, delivered_at) "
        "VALUES ('old:decided', 'sub_existing', 'delivered', 2, 3, NULL, 4)"
    )
    conn.execute(
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, ticket_id, source_kind, source_id, "
        "source_sequence, occurred_at, payload) "
        "VALUES ('old:undecided', 'worker_failed', ?, 'ticket', ?, 2, 5, "
        '\'{"ticket_title":"Existing Ticket"}\')',
        (ticket_id, ticket_id),
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
        HEAD_REVISION
    )
    assert [
        (row["fact_id"], row["subject_kind"], row["subject_id"], row["payload"])
        for row in upgraded.execute(
            "SELECT fact_id, subject_kind, COALESCE(ticket_id, agent_key) AS subject_id, payload "
            "FROM notification_facts ORDER BY fact_id"
        )
    ] == [
        (
            "old:decided",
            "ticket",
            ticket_id,
            '{"ticket_title":"Existing Ticket"}',
        ),
        (
            "old:undecided",
            "ticket",
            ticket_id,
            '{"ticket_title":"Existing Ticket"}',
        ),
    ]
    assert tuple(
        upgraded.execute(
            "SELECT outcome, decided_at FROM notification_decisions WHERE fact_id = 'old:decided'"
        ).fetchone()
    ) == ("notify", 3)
    assert tuple(
        upgraded.execute(
            "SELECT route, tag FROM notification_intents WHERE fact_id = 'old:decided'"
        ).fetchone()
    ) == ("/#/ticket/existing", "panels-ticket-existing")
    assert tuple(
        upgraded.execute(
            "SELECT status, attempts, delivered_at FROM notification_deliveries "
            "WHERE fact_id = 'old:decided'"
        ).fetchone()
    ) == ("delivered", 2, 4)
    assert (
        upgraded.execute(
            "SELECT sequence FROM notification_projection_cursors "
            "WHERE source_kind = 'conversation' AND source_id = 'c_existing_chief'"
        ).fetchone()[0]
        == 1
    )
    notifications_data.project_facts(upgraded)
    assert upgraded.execute("SELECT COUNT(*) FROM notification_facts").fetchone()[0] == 2
    assert notifications_data.apply_policy(upgraded, 6) == 1
    assert tuple(
        upgraded.execute(
            "SELECT outcome FROM notification_decisions WHERE fact_id = 'old:undecided'"
        ).fetchone()
    ) == ("notify",)
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
