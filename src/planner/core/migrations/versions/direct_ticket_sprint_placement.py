"""Restore direct Ticket Sprint placement and retire fallback Other items.

Revision ID: direct_ticket_sprint_placement
Revises: ticket_judgment_trouble_notes
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.engine import Connection

revision = "direct_ticket_sprint_placement"
down_revision = "ticket_judgment_trouble_notes"
branch_labels = None
depends_on = None


def _require_no_rows(connection: Connection, query: str, message: str) -> None:
    if connection.exec_driver_sql(query).first() is not None:
        raise RuntimeError(f"direct Ticket placement migration failed: {message}")


def upgrade() -> None:
    connection = op.get_bind()
    op.execute(
        "CREATE TEMP TABLE _direct_ticket_placement_before AS "
        "SELECT tickets.id, sprint_items.sprint_id, "
        "COALESCE(sprint_items.project_id, tickets.project_id) AS project_id "
        "FROM tickets LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id"
    )
    op.execute(
        "CREATE TEMP TABLE _direct_schedule_placement_before AS "
        "SELECT s.id, s.worker_type, COALESCE(i.project_id, s.project_id) AS project_id, "
        "i.sprint_id AS sprint_id, i.kind AS item_kind FROM scheduled_ticket_schedules s "
        "LEFT JOIN sprint_items i ON i.id=s.sprint_item_id"
    )
    op.execute("ALTER TABLE tickets ADD COLUMN sprint_id TEXT REFERENCES sprints(id)")
    op.execute(
        "ALTER TABLE scheduled_ticket_schedules ADD COLUMN sprint_id TEXT REFERENCES sprints(id)"
    )
    op.execute(
        "UPDATE tickets SET project_id = (SELECT project_id FROM sprint_items "
        "WHERE id = tickets.sprint_item_id), sprint_id = (SELECT sprint_id FROM sprint_items "
        "WHERE id = tickets.sprint_item_id) WHERE sprint_item_id IS NOT NULL"
    )
    op.execute(
        "UPDATE tickets SET sprint_item_id = NULL WHERE sprint_item_id IN "
        "(SELECT id FROM sprint_items WHERE kind = 'other')"
    )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET sprint_id = (SELECT sprint_id FROM "
        "sprint_items WHERE id = scheduled_ticket_schedules.sprint_item_id) "
        "WHERE sprint_item_id IS NOT NULL"
    )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET project_id = (SELECT project_id FROM "
        "sprint_items WHERE id = scheduled_ticket_schedules.sprint_item_id), "
        "placement_mode = 'current_sprint', sprint_item_id = NULL WHERE sprint_item_id IN "
        "(SELECT id FROM sprint_items WHERE kind = 'other')"
    )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET project_id = (SELECT project_id FROM "
        "sprint_items WHERE id = scheduled_ticket_schedules.sprint_item_id) "
        "WHERE sprint_item_id IS NOT NULL"
    )
    op.execute("DELETE FROM sprint_items WHERE kind = 'other'")
    op.execute(
        "INSERT OR IGNORE INTO projects (id, name, summary, priority, created_at, updated_at) "
        "VALUES ('project_personal', 'Personal', '', NULL, 0, 0)"
    )
    _require_no_rows(
        connection,
        "SELECT 1 FROM projects WHERE id='project_personal' AND name != 'Personal' COLLATE NOCASE",
        "the Personal project id is already used by another Project",
    )
    _require_no_rows(
        connection,
        "SELECT 1 FROM sprints s JOIN sprint_items i ON i.id='si_planning_' || "
        "CASE WHEN substr(s.id,1,3)='sp_' THEN substr(s.id,4) ELSE s.id END "
        "WHERE i.title != 'Planning' OR i.project_id != 'project_personal' OR i.sprint_id != s.id",
        "a deterministic Planning item id is already used by another item",
    )
    op.execute(
        "INSERT OR IGNORE INTO sprint_items (id, title, body, priority, deadline, "
        "project_id, sprint_id, kind, created_at, updated_at) "
        "SELECT 'si_planning_' || CASE WHEN substr(id, 1, 3) = 'sp_' THEN substr(id, 4) "
        "ELSE id END, 'Planning', '', 'P2', NULL, 'project_personal', id, 'normal', 0, 0 "
        "FROM sprints"
    )
    op.execute(
        "UPDATE tickets SET project_id = 'project_personal', "
        "sprint_item_id = 'si_planning_' || CASE WHEN substr(sprint_id, 1, 3) = 'sp_' "
        "THEN substr(sprint_id, 4) ELSE sprint_id END WHERE sprint_id IS NOT NULL AND "
        "worker_type IN ('planning-day','planning-midday-check','planning-sprint')"
    )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET project_id = 'project_personal', "
        "placement_mode = 'current_sprint', sprint_item_id = NULL WHERE worker_type IN "
        "('planning-day','planning-midday-check','planning-sprint')"
    )
    _require_no_rows(
        connection,
        "SELECT 1 FROM _direct_ticket_placement_before b LEFT JOIN tickets t ON t.id=b.id "
        "WHERE t.id IS NULL OR t.sprint_id IS NOT b.sprint_id OR "
        "(t.worker_type NOT IN ('planning-day','planning-midday-check','planning-sprint') "
        "AND t.project_id IS NOT b.project_id)",
        "a Ticket lost its effective placement",
    )
    _require_no_rows(
        connection,
        "SELECT 1 WHERE (SELECT count(*) FROM scheduled_ticket_schedules) != "
        "(SELECT count(*) FROM _direct_schedule_placement_before)",
        "the scheduled Ticket row count changed",
    )
    _require_no_rows(
        connection,
        "SELECT 1 FROM _direct_schedule_placement_before b LEFT JOIN "
        "scheduled_ticket_schedules s ON s.id=b.id LEFT JOIN sprint_items i ON "
        "i.id=s.sprint_item_id WHERE s.id IS NULL OR (b.worker_type NOT IN "
        "('planning-day','planning-midday-check','planning-sprint') AND "
        "COALESCE(i.project_id,s.project_id) IS NOT b.project_id) OR "
        "s.sprint_id IS NOT b.sprint_id",
        "a scheduled Ticket lost its effective placement",
    )
    _require_no_rows(connection, "SELECT 1 FROM sprint_items WHERE kind='other'", "an Other item remains")
    _require_no_rows(
        connection,
        "SELECT 1 FROM tickets t JOIN sprint_items i ON i.id=t.sprint_item_id "
        "WHERE t.project_id IS NOT i.project_id OR t.sprint_id IS NOT i.sprint_id",
        "a classified Ticket is incoherent",
    )
    _require_no_rows(
        connection,
        "SELECT 1 FROM sprints s LEFT JOIN sprint_items i ON i.id='si_planning_' || "
        "CASE WHEN substr(s.id,1,3)='sp_' THEN substr(s.id,4) ELSE s.id END "
        "WHERE i.id IS NULL OR i.project_id != 'project_personal'",
        "a Sprint lacks its Planning item",
    )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"direct Ticket placement migration failed: {violations!r}")
    op.execute("DROP TABLE _direct_ticket_placement_before")
    op.execute("DROP TABLE _direct_schedule_placement_before")


def downgrade() -> None:
    raise NotImplementedError("direct placement cannot be removed without recreating false Other items")
