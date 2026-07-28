"""Make Sprint Items the only durable path from Tickets into Sprints.

Every directly placed Ticket and scheduled-Ticket template is moved under one shared
``Other`` Sprint Item for its sprint/project pair.  The two direct ``sprint_id`` columns
then leave the schema.

Revision ID: sprint_item_only_placement
Revises: day_midday_reconciliation
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

revision = "sprint_item_only_placement"
down_revision = "day_midday_reconciliation"
branch_labels = None
depends_on = None

OTHER_PROJECT_ID = "project_other"
OTHER_INDEX_NAME = "idx_sprint_items_one_other_per_sprint_project"


def tickets_table() -> Table:
    """The complete pre-revision Ticket table used as the batch-copy source."""
    table = Table(
        "tickets",
        MetaData(),
        Column("id", Text, primary_key=True, nullable=True),
        Column("title", Text, nullable=False),
        Column("worker_type", Text, nullable=False),
        Column("employee_backend", Text, nullable=False),
        Column("employee_launch_model", Text),
        Column("employee_launch_reasoning_effort", Text),
        Column("stage", Text, nullable=False, server_default=text("'needs_kickoff'")),
        Column("priority", Text, nullable=False, server_default=text("'P3'")),
        Column("deadline", Text),
        Column("project_id", Text, ForeignKey("projects.id")),
        Column("sprint_item_id", Text, ForeignKey("sprint_items.id")),
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        Column("recap", Text, nullable=False, server_default=text("''")),
        Column("ceiling", Text, nullable=False),
        Column("at_cap", Text, nullable=False, server_default=text("'propose'")),
        Column("ticket_status", Text, nullable=False, server_default=text("'empty'")),
        Column("backend_error", Text),
        Column("stage_ownership_overrides", Text, nullable=False, server_default=text("'{}'")),
        Column("default_stage_ownership_mode", Text),
        Column("conversation_id", Text),
        Column("alias", Text),
        Column("fields", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        Column(
            "ticket_status_changed_at",
            Integer,
            nullable=False,
            server_default=text("0"),
        ),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        CheckConstraint("at_cap IN ('stop','propose')"),
        CheckConstraint(
            "ticket_status IN "
            "('empty','blocked','agent','paired','awaiting_approval','needs_user','user','errored')"
        ),
        CheckConstraint("default_stage_ownership_mode IN ('worker','user','paired')"),
    )
    Index("idx_tickets_alias", table.c.alias, unique=True, sqlite_where=text("alias IS NOT NULL"))
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table


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
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        Column("sprint_item_id", Text, ForeignKey("sprint_items.id")),
        Column("employee_backend", Text),
        Column("employee_launch_model", Text),
        Column("blocked_by_ticket_ids", Text, nullable=False, server_default=text("'[]'")),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        CheckConstraint("enabled IN (0, 1)"),
        CheckConstraint(
            "cadence IN ('every_planning_day', 'current_sprint_final_day')"
        ),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0', 'P1', 'P2', 'P3')"),
    )
    Index(
        "idx_scheduled_ticket_schedules_slot",
        table.c.enabled,
        table.c.local_time,
    )
    return table


def _require_no_rows(connection: Connection, query: str, message: str) -> None:
    if connection.exec_driver_sql(query).first() is not None:
        raise RuntimeError(f"sprint-item placement migration reconciliation failed: {message}")


def _snapshot_effective_placements() -> None:
    # A direct sprint placement wins for the rows this revision is specifically moving.
    # All other rows snapshot the same parent-derived placement the application exposes.
    op.execute(
        """
        CREATE TEMP TABLE _sprint_item_migration_tickets AS
        SELECT
          tickets.id,
          CASE
            WHEN tickets.sprint_id IS NOT NULL THEN tickets.sprint_id
            ELSE sprint_items.sprint_id
          END AS effective_sprint_id,
          CASE
            WHEN tickets.sprint_id IS NOT NULL
              THEN COALESCE(tickets.project_id, 'project_other')
            WHEN tickets.sprint_item_id IS NOT NULL THEN sprint_items.project_id
            ELSE tickets.project_id
          END AS effective_project_id
        FROM tickets
        LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id
        """
    )
    op.execute(
        """
        CREATE TEMP TABLE _sprint_item_migration_schedules AS
        SELECT
          scheduled_ticket_schedules.id,
          CASE
            WHEN scheduled_ticket_schedules.sprint_id IS NOT NULL
              THEN scheduled_ticket_schedules.sprint_id
            ELSE sprint_items.sprint_id
          END AS effective_sprint_id,
          CASE
            WHEN scheduled_ticket_schedules.sprint_id IS NOT NULL
              THEN COALESCE(scheduled_ticket_schedules.project_id, 'project_other')
            WHEN scheduled_ticket_schedules.sprint_item_id IS NOT NULL
              THEN sprint_items.project_id
            ELSE scheduled_ticket_schedules.project_id
          END AS effective_project_id
        FROM scheduled_ticket_schedules
        LEFT JOIN sprint_items
          ON sprint_items.id = scheduled_ticket_schedules.sprint_item_id
        """
    )


def _ensure_other_project(connection: Connection) -> None:
    needs_other_project = """
      SELECT 1 FROM tickets WHERE sprint_id IS NOT NULL AND project_id IS NULL
      UNION ALL
      SELECT 1 FROM scheduled_ticket_schedules
       WHERE sprint_id IS NOT NULL AND project_id IS NULL
      LIMIT 1
    """
    if connection.exec_driver_sql(needs_other_project).first() is None:
        return

    _require_no_rows(
        connection,
        """
        SELECT 1 FROM projects
         WHERE name = 'Other' COLLATE NOCASE AND id != 'project_other'
         LIMIT 1
        """,
        "the canonical Other project id is absent but its name belongs to another project",
    )
    op.execute(
        """
        INSERT INTO projects (id, name, summary, created_at, updated_at)
        SELECT 'project_other', 'Other', '', 0, 0
         WHERE NOT EXISTS (SELECT 1 FROM projects WHERE id = 'project_other')
        """
    )


def _create_required_other_items() -> None:
    op.execute(
        """
        WITH required AS (
          SELECT
            sprint_id,
            COALESCE(project_id, 'project_other') AS project_id,
            created_at,
            updated_at
          FROM tickets
          WHERE sprint_id IS NOT NULL
          UNION ALL
          SELECT
            sprint_id,
            COALESCE(project_id, 'project_other') AS project_id,
            created_at,
            updated_at
          FROM scheduled_ticket_schedules
          WHERE sprint_id IS NOT NULL
        ),
        grouped AS (
          SELECT
            sprint_id,
            project_id,
            MIN(created_at) AS created_at,
            MAX(updated_at) AS updated_at
          FROM required
          GROUP BY sprint_id, project_id
        )
        INSERT INTO sprint_items (
          id, title, body, priority, deadline, project_id, sprint_id,
          created_at, updated_at, kind
        )
        SELECT
          'si_other_' || lower(hex(randomblob(12))),
          'Other',
          '',
          'P3',
          NULL,
          grouped.project_id,
          grouped.sprint_id,
          grouped.created_at,
          grouped.updated_at,
          'other'
        FROM grouped
        WHERE NOT EXISTS (
          SELECT 1
          FROM sprint_items
          WHERE sprint_items.kind = 'other'
            AND sprint_items.sprint_id = grouped.sprint_id
            AND sprint_items.project_id = grouped.project_id
        )
        """
    )


def _move_direct_placements_to_items() -> None:
    op.execute(
        """
        UPDATE tickets
        SET sprint_item_id = (
          SELECT sprint_items.id
          FROM sprint_items
          WHERE sprint_items.kind = 'other'
            AND sprint_items.sprint_id = tickets.sprint_id
            AND sprint_items.project_id = COALESCE(tickets.project_id, 'project_other')
        )
        WHERE sprint_id IS NOT NULL
        """
    )
    op.execute("UPDATE tickets SET project_id = NULL WHERE sprint_item_id IS NOT NULL")

    op.execute(
        """
        UPDATE scheduled_ticket_schedules
        SET sprint_item_id = (
          SELECT sprint_items.id
          FROM sprint_items
          WHERE sprint_items.kind = 'other'
            AND sprint_items.sprint_id = scheduled_ticket_schedules.sprint_id
            AND sprint_items.project_id =
                COALESCE(scheduled_ticket_schedules.project_id, 'project_other')
        )
        WHERE sprint_id IS NOT NULL
        """
    )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET project_id = NULL "
        "WHERE sprint_item_id IS NOT NULL"
    )


def _reconcile(connection: Connection) -> None:
    _require_no_rows(
        connection,
        """
        SELECT 1
        WHERE (SELECT COUNT(*) FROM tickets)
           != (SELECT COUNT(*) FROM _sprint_item_migration_tickets)
        """,
        "the Ticket row count changed",
    )
    _require_no_rows(
        connection,
        """
        SELECT 1
        WHERE (SELECT COUNT(*) FROM scheduled_ticket_schedules)
           != (SELECT COUNT(*) FROM _sprint_item_migration_schedules)
        """,
        "the scheduled-Ticket row count changed",
    )
    _require_no_rows(
        connection,
        """
        SELECT 1
        FROM _sprint_item_migration_tickets AS before
        LEFT JOIN tickets ON tickets.id = before.id
        LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id
        WHERE tickets.id IS NULL
           OR sprint_items.sprint_id IS NOT before.effective_sprint_id
           OR COALESCE(sprint_items.project_id, tickets.project_id)
                IS NOT before.effective_project_id
        LIMIT 1
        """,
        "a Ticket row or its effective sprint/project placement changed",
    )
    _require_no_rows(
        connection,
        """
        SELECT 1
        FROM _sprint_item_migration_schedules AS before
        LEFT JOIN scheduled_ticket_schedules AS schedules ON schedules.id = before.id
        LEFT JOIN sprint_items ON sprint_items.id = schedules.sprint_item_id
        WHERE schedules.id IS NULL
           OR sprint_items.sprint_id IS NOT before.effective_sprint_id
           OR COALESCE(sprint_items.project_id, schedules.project_id)
                IS NOT before.effective_project_id
        LIMIT 1
        """,
        "a scheduled-Ticket row or its effective sprint/project placement changed",
    )
    _require_no_rows(
        connection,
        """
        SELECT 1
        FROM sprint_items
        WHERE kind = 'other' AND sprint_id IS NOT NULL
        GROUP BY sprint_id, project_id
        HAVING COUNT(*) != 1
        LIMIT 1
        """,
        "a sprint/project pair has more than one Other item",
    )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            "sprint-item placement migration reconciliation failed: "
            f"foreign key violations remain: {violations!r}"
        )


def upgrade() -> None:
    connection = op.get_bind()
    _snapshot_effective_placements()
    _ensure_other_project(connection)

    op.execute(
        "ALTER TABLE sprint_items ADD COLUMN kind TEXT NOT NULL DEFAULT 'normal' "
        "CHECK (kind IN ('normal','other'))"
    )
    op.execute(
        f"CREATE UNIQUE INDEX {OTHER_INDEX_NAME} "
        "ON sprint_items(sprint_id, project_id) "
        "WHERE kind = 'other' AND sprint_id IS NOT NULL"
    )

    _create_required_other_items()
    _move_direct_placements_to_items()

    with op.batch_alter_table(
        "tickets", copy_from=tickets_table(), recreate="always"
    ) as batch_op:
        batch_op.drop_column("sprint_id")
    with op.batch_alter_table(
        "scheduled_ticket_schedules",
        copy_from=scheduled_ticket_schedules_table(),
        recreate="always",
    ) as batch_op:
        batch_op.drop_column("sprint_id")
        batch_op.add_column(
            Column(
                "placement_mode",
                Text,
                nullable=False,
                server_default=text("'current_sprint'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_scheduled_ticket_placement_mode",
            "placement_mode IN ('current_sprint','backlog','sprint_item')",
        )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET placement_mode = 'sprint_item' "
        "WHERE sprint_item_id IS NOT NULL"
    )

    _reconcile(connection)
    op.execute("DROP TABLE _sprint_item_migration_tickets")
    op.execute("DROP TABLE _sprint_item_migration_schedules")


def downgrade() -> None:
    raise NotImplementedError(
        "direct sprint placement was merged into shared Other items and cannot be "
        "reconstructed without inventing which rows were formerly standalone"
    )
