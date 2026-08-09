"""Add the sprint day-four cadence and seed its Checkpoint schedule.

The schedule-table rebuild expands only the cadence constraint. Existing schedules and
their occurrence receipts keep their identities and values. The seeded personal Ticket
stays user-owned and uses the scheduler's existing exact-slot behavior.

Revision ID: weekly_sprint_checkpoint_schedule
Revises: notification_preferences_by_subject
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.engine import Connection

revision = "weekly_sprint_checkpoint_schedule"
down_revision = "notification_preferences_by_subject"
branch_labels = None
depends_on = None

CHECKPOINT_SCHEDULE_ID = "schedule_weekly_sprint_checkpoint"


def scheduled_ticket_schedules_table() -> Table:
    """The complete pre-revision schedule table used as the batch-copy source."""
    table = Table(
        "scheduled_ticket_schedules",
        MetaData(),
        Column("id", Text, primary_key=True, nullable=True),
        Column("enabled", Integer, nullable=False),
        Column("cadence", Text, nullable=False),
        Column("local_time", Text, nullable=False),
        Column("title", Text, nullable=False),
        Column("worker_type", Text, nullable=False),
        Column("kickoff_note", Text, nullable=False, server_default=text("''")),
        Column("priority", Text, nullable=False),
        Column("deadline", Text),
        Column("project_id", Text, ForeignKey("projects.id")),
        Column("sprint_item_id", Text, ForeignKey("sprint_items.id")),
        Column("employee_backend", Text),
        Column("employee_launch_model", Text),
        Column("blocked_by_ticket_ids", Text, nullable=False, server_default=text("'[]'")),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        Column(
            "placement_mode",
            Text,
            nullable=False,
            server_default=text("'current_sprint'"),
        ),
        CheckConstraint("enabled IN (0, 1)"),
        CheckConstraint(
            "cadence IN ('every_planning_day', 'current_sprint_final_day')",
            name="ck_scheduled_ticket_cadence",
        ),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0', 'P1', 'P2', 'P3')"),
        CheckConstraint(
            "placement_mode IN ('current_sprint','backlog','sprint_item')",
            name="ck_scheduled_ticket_placement_mode",
        ),
    )
    Index(
        "idx_scheduled_ticket_schedules_slot",
        table.c.enabled,
        table.c.local_time,
    )
    return table


def _require_available_seed_id(connection: Connection) -> None:
    existing = connection.exec_driver_sql(
        "SELECT 1 FROM scheduled_ticket_schedules WHERE id = ?",
        (CHECKPOINT_SCHEDULE_ID,),
    ).first()
    if existing is not None:
        raise RuntimeError(
            "weekly sprint migration cannot seed the Checkpoint schedule because its "
            "reserved id already exists"
        )


def upgrade() -> None:
    connection = op.get_bind()
    _require_available_seed_id(connection)

    with op.batch_alter_table(
        "scheduled_ticket_schedules",
        copy_from=scheduled_ticket_schedules_table(),
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint("ck_scheduled_ticket_cadence", type_="check")
        batch_op.create_check_constraint(
            "ck_scheduled_ticket_cadence",
            "cadence IN ("
            "'every_planning_day',"
            "'current_sprint_day_four',"
            "'current_sprint_final_day'"
            ")",
        )

    op.execute(
        f"""
        INSERT INTO scheduled_ticket_schedules (
          id, enabled, cadence, local_time, title, worker_type, kickoff_note,
          priority, deadline, project_id, sprint_item_id, employee_backend,
          employee_launch_model, blocked_by_ticket_ids, created_at, updated_at,
          placement_mode
        ) VALUES (
          '{CHECKPOINT_SCHEDULE_ID}', 1, 'current_sprint_day_four', '17:00',
          'Checkpoint', 'personal',
          'Review the sprint so far and decide what to adjust for the remaining days.',
          'P3', NULL, NULL, NULL, NULL, NULL, '[]',
          0, 0, 'current_sprint'
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "the day-four cadence can carry user schedules and cannot be removed safely"
    )
