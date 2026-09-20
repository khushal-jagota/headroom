"""The subject-keyed preference migration preserves choices and notification history."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema
from planner.notifications import data as notifications_data

PREVIOUS_REVISION = "backend_usage_and_model_enablement"


def _upgrade_to(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), revision)
    engine.dispose()


def test_upgrade_keys_preferences_by_subject_and_preserves_notification_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "notification-preferences-by-subject.db"
    _upgrade_to(db_path, PREVIOUS_REVISION)
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
    conn.executemany(
        "INSERT INTO notification_preferences"
        "(notification_type, enabled, updated_at) VALUES (?, ?, ?)",
        (
            ("ticket_needs_approval", 0, 11),
            ("ticket_needs_input", 1, 12),
            ("permission_requested", 0, 13),
            ("worker_completed", 1, 14),
            ("worker_failed", 0, 15),
        ),
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
        "INSERT INTO notification_projection_cursors(source_kind, source_id, sequence) "
        "VALUES ('conversation', 'c_existing_chief', 1)"
    )
    conn.execute(
        "INSERT INTO notification_projection_cursors(source_kind, source_id, sequence) "
        "VALUES ('ticket', ?, 0)",
        (ticket_id,),
    )
    conn.execute(
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, subject_kind, ticket_id, agent_key, source_kind, "
        "source_id, source_sequence, occurred_at, payload) "
        "VALUES ('old:ticket-input', 'ticket_needs_input', 'ticket', ?, NULL, "
        "'ticket', ?, 0, 2, '{\"ticket_title\":\"Existing Ticket\"}')",
        (ticket_id, ticket_id),
    )
    conn.execute(
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, subject_kind, ticket_id, agent_key, source_kind, "
        "source_id, source_sequence, occurred_at, payload) "
        "VALUES ('old:chief-input', 'ticket_needs_input', 'agent', NULL, "
        "'chief_of_staff', 'conversation', 'c_existing_chief', 1, 3, "
        '\'{"subject_label":"Chief of Staff"}\')'
    )
    conn.execute(
        "INSERT INTO notification_decisions(fact_id, outcome, decided_at) "
        "VALUES ('old:chief-input', 'notify', 4)"
    )
    conn.execute(
        "INSERT INTO notification_intents"
        "(fact_id, title, body, route, tag, created_at) "
        "VALUES ('old:chief-input', 'Panels', 'Chief reply.', '/#/chief-of-staff', "
        "'panels-agent-chief_of_staff', 4)"
    )
    conn.execute(
        "INSERT INTO notification_push_subscriptions"
        "(subscription_id, endpoint, p256dh, auth, created_at, updated_at, disabled_at) "
        "VALUES ('sub_existing', 'https://push.example/existing', 'key', 'auth', 1, 2, NULL)"
    )
    conn.execute(
        "INSERT INTO notification_deliveries"
        "(fact_id, subscription_id, status, attempts, next_attempt_at, "
        "last_error, delivered_at) "
        "VALUES ('old:chief-input', 'sub_existing', 'delivered', 2, 5, NULL, 6)"
    )
    conn.close()

    # The assertions run at the last revision that still has the notification history
    # this migration preserves. one_notification_path drops it after that.
    _upgrade_to(db_path, "derive_ticket_status")
    upgraded = connect(str(db_path))

    assert [
        tuple(row)
        for row in upgraded.execute(
            "SELECT subject_key, notification_type, enabled, updated_at "
            "FROM notification_preferences ORDER BY subject_key, notification_type"
        )
    ] == [
        ("chief_of_staff", "awaiting_reply", 1, 13),
        ("chief_of_staff", "errored", 0, 15),
        ("sprint_item_supervisors", "errored", 0, 0),
        ("tickets", "awaiting_approval", 0, 11),
        ("tickets", "awaiting_reply", 1, 13),
        ("tickets", "errored", 0, 15),
    ]
    assert [
        tuple(row)
        for row in upgraded.execute(
            "SELECT fact_id, notification_type, subject_kind, "
            "COALESCE(ticket_id, agent_key), payload "
            "FROM notification_facts ORDER BY fact_id"
        )
    ] == [
        (
            "old:chief-input",
            "needs_input",
            "agent",
            "chief_of_staff",
            '{"subject_label":"Chief of Staff"}',
        ),
    ]
    assert tuple(
        upgraded.execute(
            "SELECT outcome, decided_at FROM notification_decisions "
            "WHERE fact_id = 'old:chief-input'"
        ).fetchone()
    ) == ("notify", 4)
    assert tuple(
        upgraded.execute(
            "SELECT title, body, route, tag, created_at FROM notification_intents "
            "WHERE fact_id = 'old:chief-input'"
        ).fetchone()
    ) == (
        "Panels",
        "Chief reply.",
        "/#/chief-of-staff",
        "panels-agent-chief_of_staff",
        4,
    )
    assert tuple(
        upgraded.execute(
            "SELECT endpoint, p256dh, auth, created_at, updated_at, disabled_at "
            "FROM notification_push_subscriptions WHERE subscription_id = 'sub_existing'"
        ).fetchone()
    ) == ("https://push.example/existing", "key", "auth", 1, 2, None)
    assert tuple(
        upgraded.execute(
            "SELECT status, attempts, next_attempt_at, last_error, delivered_at "
            "FROM notification_deliveries WHERE fact_id = 'old:chief-input'"
        ).fetchone()
    ) == ("delivered", 2, 5, None, 6)
    assert [
        tuple(row)
        for row in upgraded.execute(
            "SELECT source_kind, source_id, sequence "
            "FROM notification_projection_cursors "
            "WHERE source_id IN (?, 'c_existing_chief') ORDER BY source_kind",
            (ticket_id,),
        )
    ] == [
        ("conversation", "c_existing_chief", 1),
        ("ticket", ticket_id, 0),
    ]
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()

    # And the rest of the chain still runs over those same rows.
    head = connect(str(db_path))
    create_schema(head)
    assert notifications_data.queue_deliveries(head, 7) == 0
    assert head.execute("PRAGMA foreign_key_check").fetchall() == []
    head.close()
