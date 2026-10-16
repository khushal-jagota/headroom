"""Retire the reviewed obsolete planning cohort without declaring success.

Revision ID: retire_planning_workers
Revises: a_refused_codex_login_outlives_a_restart
"""

from __future__ import annotations

from alembic import op

from planner.core.migrations.planning_retirement import retire_planning_workers

revision = "retire_planning_workers"
down_revision = "a_refused_codex_login_outlives_a_restart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Validate and retire before any schema mutation. An outstanding claim aborts startup.
    retire_planning_workers(conn)
    conn.exec_driver_sql(
        "ALTER TABLE sprint_items ADD COLUMN settled INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0,1))"
    )
    conn.exec_driver_sql(
        "ALTER TABLE tickets ADD COLUMN orchestrator_kind TEXT NOT NULL DEFAULT 'chief' CHECK (orchestrator_kind IN ('chief','sprint_item'))"
    )
    conn.exec_driver_sql(
        "UPDATE tickets SET orchestrator_kind='sprint_item' WHERE sprint_item_id IS NOT NULL"
    )
    # SQLite cannot add a cross-column CHECK. These equivalent triggers preserve the
    # existing table identity, all foreign keys, and every historical row.
    for action in ("INSERT", "UPDATE"):
        conn.exec_driver_sql(
            f"CREATE TRIGGER tickets_assignment_{action.lower()} BEFORE {action} ON tickets "
            "WHEN NOT ((NEW.orchestrator_kind='chief' AND NEW.sprint_item_id IS NULL) OR "
            "(NEW.orchestrator_kind='sprint_item' AND NEW.sprint_item_id IS NOT NULL)) "
            "BEGIN SELECT RAISE(ABORT,'invalid orchestrator assignment'); END"
        )

    original_sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_ticket_schedules'"
    ).scalar_one()
    old_cadence = (
        "cadence IN ('every_planning_day', 'current_sprint_day_four', 'current_sprint_final_day')"
    )
    if original_sql.count(old_cadence) != 1:
        raise RuntimeError("schedule cadence constraint changed; migration needs review")
    replacement_sql = original_sql.replace(
        old_cadence,
        "cadence IN ('daily','every_planning_day','current_sprint_day_four',"
        "'current_sprint_final_day')",
    )
    first_paren = replacement_sql.index("(")
    replacement_sql = (
        "CREATE TABLE scheduled_ticket_schedules_new ("
        "orchestrator_kind TEXT NOT NULL DEFAULT 'chief' "
        "CHECK (orchestrator_kind IN ('chief','sprint_item'))," + replacement_sql[first_paren + 1 :]
    )
    columns = [
        str(row[1]) for row in conn.exec_driver_sql("PRAGMA table_info(scheduled_ticket_schedules)")
    ]
    indexes = [
        row[0]
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='scheduled_ticket_schedules' AND sql IS NOT NULL"
        )
    ]
    conn.exec_driver_sql(replacement_sql)
    quoted_columns = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
    conn.exec_driver_sql(
        f"INSERT INTO scheduled_ticket_schedules_new ({quoted_columns}) "
        f"SELECT {quoted_columns} FROM scheduled_ticket_schedules"
    )
    conn.exec_driver_sql("DROP TABLE scheduled_ticket_schedules")
    conn.exec_driver_sql(
        "ALTER TABLE scheduled_ticket_schedules_new RENAME TO scheduled_ticket_schedules"
    )
    for index_sql in indexes:
        conn.exec_driver_sql(index_sql)
    conn.exec_driver_sql(
        "UPDATE scheduled_ticket_schedules SET orchestrator_kind='sprint_item' "
        "WHERE sprint_item_id IS NOT NULL"
    )
    conn.exec_driver_sql(
        "UPDATE scheduled_ticket_schedules SET enabled=0 WHERE "
        "worker_type IN ('planning-day','planning-midday-check','planning-sprint') OR "
        "cadence IN ('current_sprint_day_four','current_sprint_final_day')"
    )
    conn.exec_driver_sql(
        "UPDATE scheduled_ticket_schedules SET cadence='daily' WHERE "
        "cadence='every_planning_day' AND worker_type NOT IN "
        "('planning-day','planning-midday-check','planning-sprint')"
    )
    for action in ("INSERT", "UPDATE"):
        conn.exec_driver_sql(
            f"CREATE TRIGGER schedules_assignment_{action.lower()} BEFORE {action} "
            "ON scheduled_ticket_schedules WHEN NOT "
            "((NEW.orchestrator_kind='chief' AND NEW.sprint_item_id IS NULL) OR "
            "(NEW.orchestrator_kind='sprint_item' AND NEW.sprint_item_id IS NOT NULL)) "
            "BEGIN SELECT RAISE(ABORT,'invalid schedule orchestrator assignment'); END"
        )


def downgrade() -> None:
    raise RuntimeError(
        "Planning retirement is forward-only; restore the verified pre-cutover backup instead"
    )
