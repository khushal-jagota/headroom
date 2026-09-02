"""The drop revision takes the watch switch and the ping, and keeps decided history."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect
from planner.notifications import data as notifications_data

PREVIOUS_REVISION = "ticket_wakes_supervisor"
HEAD_REVISION = "planning_day_direction"


def _upgrade_to(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), revision)
    engine.dispose()


def _database_with_a_ping_behind_it(tmp_path: Path, name: str) -> Path:
    """A database at the revision before the drop, holding both ping facts.

    One ping was decided and pushed. The other was projected and never decided, which is
    the state the notification loop would choke on once the type is unknown.
    """
    db_path = tmp_path / name
    _upgrade_to(db_path, PREVIOUS_REVISION)
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) "
        "VALUES ('project_example', 'Example', 1, 1)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id, title, body, priority, project_id, created_at, "
        "updated_at, supervisor_ping_sequence, supervisor_ping_at) "
        "VALUES ('si_existing', 'Existing Item', '', 'P2', 'project_example', 1, 1, 7, 8)"
    )
    for fact_id, sequence in (("sprint_item_ping:si_existing:6", 6),
                              ("sprint_item_ping:si_existing:7", 7)):
        conn.execute(
            "INSERT INTO notification_facts"
            "(fact_id, notification_type, subject_kind, sprint_item_id, source_kind, "
            "source_id, source_sequence, occurred_at, payload) "
            "VALUES (?, 'sprint_item_ping', 'sprint_item', 'si_existing', "
            "'sprint_item_ping', 'si_existing', ?, 8, '{\"subject_label\":\"Existing Item\"}')",
            (fact_id, sequence),
        )
    conn.execute(
        "INSERT INTO notification_decisions(fact_id, outcome, decided_at) "
        "VALUES ('sprint_item_ping:si_existing:6', 'notify', 9)"
    )
    conn.execute(
        "INSERT INTO notification_intents(fact_id, title, body, route, tag, created_at) "
        "VALUES ('sprint_item_ping:si_existing:6', 'Panels', 'Existing Item wants you.', "
        "'/#/workspace/item/si_existing', 'panels-sprint_item-si_existing', 9)"
    )
    conn.execute(
        "INSERT INTO notification_projection_cursors(source_kind, source_id, sequence) "
        "VALUES ('sprint_item_ping', 'si_existing', 7)"
    )
    conn.execute(
        "INSERT INTO notification_preferences"
        "(subject_key, notification_type, enabled, updated_at) "
        "VALUES ('sprint_item_supervisors', 'sprint_item_ping', 1, 4)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_upgrade_drops_the_watch_switch_and_both_ping_columns(tmp_path: Path) -> None:
    db_path = _database_with_a_ping_behind_it(tmp_path, "wake-and-ping-columns.db")

    _upgrade_to(db_path, HEAD_REVISION)

    conn = connect(str(db_path))
    ticket_columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    item_columns = {row[1] for row in conn.execute("PRAGMA table_info(sprint_items)")}
    assert "wakes_supervisor" not in ticket_columns
    assert "supervisor_ping_sequence" not in item_columns
    assert "supervisor_ping_at" not in item_columns
    conn.close()


def test_upgrade_clears_the_ping_ahead_of_the_loop_and_keeps_the_decided_one(
    tmp_path: Path,
) -> None:
    db_path = _database_with_a_ping_behind_it(tmp_path, "wake-and-ping-facts.db")

    _upgrade_to(db_path, HEAD_REVISION)

    conn = connect(str(db_path))
    # The decided ping is finished business and stays, with its decision and its push.
    assert (
        conn.execute(
            "SELECT count(*) AS total FROM notification_facts "
            "WHERE fact_id = 'sprint_item_ping:si_existing:6'"
        ).fetchone()["total"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT route FROM notification_intents "
            "WHERE fact_id = 'sprint_item_ping:si_existing:6'"
        ).fetchone()["route"]
        == "/#/workspace/item/si_existing"
    )
    # The undecided one is gone, because nothing can decide it now.
    assert (
        conn.execute(
            "SELECT count(*) AS total FROM notification_facts "
            "WHERE fact_id = 'sprint_item_ping:si_existing:7'"
        ).fetchone()["total"]
        == 0
    )
    # The stored switch is gone from the table, not merely absent from the catalogue.
    assert (
        conn.execute(
            "SELECT count(*) AS total FROM notification_preferences "
            "WHERE notification_type = 'sprint_item_ping'"
        ).fetchone()["total"]
        == 0
    )
    preferences = notifications_data.resolved_preferences(conn)
    assert ("sprint_item_supervisors", "sprint_item_ping") not in preferences
    assert preferences[("sprint_item_supervisors", "worker_failed")] is False
    # Deciding the remaining facts is the loop's real move, and it no longer raises.
    notifications_data.apply_policy(conn, now=20)
    conn.close()
