"""The ping revision keeps notification history and adds no push nobody asked for."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect
from planner.notifications import data as notifications_data

PREVIOUS_REVISION = "one_approval_gate"
HEAD_REVISION = "sprint_item_supervisor_ping"


def _upgrade_to(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), revision)
    engine.dispose()


def test_upgrade_keeps_history_and_leaves_supervisor_failures_off(tmp_path: Path) -> None:
    db_path = tmp_path / "supervisor-ping.db"
    _upgrade_to(db_path, PREVIOUS_REVISION)
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_chief', 'codex', '/tmp/workspace', 'full', 1, 1)"
    )
    conn.execute(
        "INSERT INTO agents(agent_key, conversation_id) VALUES ('chief_of_staff', 'c_chief')"
    )
    conn.execute(
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, subject_kind, agent_key, source_kind, source_id, "
        "source_sequence, occurred_at, payload) "
        "VALUES ('conversation:c_chief:1', 'worker_completed', 'agent', 'chief_of_staff', "
        '\'conversation\', \'c_chief\', 1, 2, \'{"subject_label":"Chief of Staff"}\')'
    )
    conn.execute(
        "INSERT INTO notification_decisions(fact_id, outcome, decided_at) "
        "VALUES ('conversation:c_chief:1', 'notify', 3)"
    )
    conn.execute(
        "INSERT INTO notification_intents(fact_id, title, body, route, tag, created_at) "
        "VALUES ('conversation:c_chief:1', 'Panels', 'Chief of Staff has a completed worker "
        "reply.', '/#/agents/chief-of-staff', 'panels-agent-chief_of_staff', 3)"
    )
    conn.execute(
        "INSERT INTO notification_preferences"
        "(subject_key, notification_type, enabled, updated_at) "
        "VALUES ('tickets', 'worker_completed', 0, 4)"
    )
    conn.commit()
    conn.close()

    _upgrade_to(db_path, HEAD_REVISION)

    conn = connect(str(db_path))
    # Durable history survives, subject and delivery graph intact.
    fact = conn.execute(
        "SELECT subject_kind, agent_key, sprint_item_id FROM notification_facts "
        "WHERE fact_id = 'conversation:c_chief:1'"
    ).fetchone()
    assert (fact["subject_kind"], fact["agent_key"], fact["sprint_item_id"]) == (
        "agent",
        "chief_of_staff",
        None,
    )
    assert (
        conn.execute(
            "SELECT outcome FROM notification_decisions WHERE fact_id = 'conversation:c_chief:1'"
        ).fetchone()["outcome"]
        == "notify"
    )
    assert (
        conn.execute(
            "SELECT route FROM notification_intents WHERE fact_id = 'conversation:c_chief:1'"
        ).fetchone()["route"]
        == "/#/agents/chief-of-staff"
    )

    # A saved choice keeps its value, and the new subject arrives with the ping on and
    # failures off: about thirty supervisor turns fail a day, and nobody asked for those.
    preferences = notifications_data.resolved_preferences(conn)
    assert preferences[("tickets", "worker_completed")] is False
    assert preferences[("sprint_item_supervisors", "sprint_item_ping")] is True
    assert preferences[("sprint_item_supervisors", "worker_failed")] is False
    conn.close()


def test_upgrade_gives_every_item_a_ping_that_has_not_happened(tmp_path: Path) -> None:
    db_path = tmp_path / "supervisor-ping-columns.db"
    _upgrade_to(db_path, PREVIOUS_REVISION)
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) "
        "VALUES ('project_example', 'Example', 1, 1)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id, title, body, priority, project_id, created_at, "
        "updated_at) VALUES ('si_existing', 'Existing Item', '', 'P2', 'project_example', 1, 1)"
    )
    conn.commit()
    conn.close()

    _upgrade_to(db_path, HEAD_REVISION)

    conn = connect(str(db_path))
    row = conn.execute(
        "SELECT supervisor_ping_sequence, supervisor_ping_at FROM sprint_items "
        "WHERE id = 'si_existing'"
    ).fetchone()
    assert (row["supervisor_ping_sequence"], row["supervisor_ping_at"]) == (None, None)

    # No Item has ever pinged, so the upgrade produces no ping fact at all.
    notifications_data.project_facts(conn)
    assert (
        conn.execute(
            "SELECT count(*) AS total FROM notification_facts "
            "WHERE notification_type = 'sprint_item_ping'"
        ).fetchone()["total"]
        == 0
    )
    conn.close()
