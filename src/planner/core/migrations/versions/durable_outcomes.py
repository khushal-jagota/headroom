"""Separate durable Outcome context from Sprint commitments without moving history.

Revision ID: durable_outcomes
Revises: ticket_guidance
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

revision = "durable_outcomes"
down_revision = "ticket_guidance"
branch_labels = None
depends_on = None


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
        Column(
            "placement_mode",
            Text,
            nullable=False,
            server_default=text("'current_sprint'"),
        ),
        CheckConstraint("enabled IN (0, 1)"),
        CheckConstraint(
            "cadence IN ('every_planning_day', 'current_sprint_day_four', 'current_sprint_final_day')",
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


def upgrade() -> None:
    connection = op.get_bind()
    for table in (
        "tickets",
        "sprint_items",
        "agents",
        "scheduled_ticket_schedules",
        "scheduled_ticket_occurrences",
    ):
        op.execute(f"CREATE TEMP TABLE _outcome_before_{table} AS SELECT * FROM {table}")
    op.execute(
        "CREATE TABLE sprint_outcomes (sprint_id TEXT NOT NULL REFERENCES sprints(id) ON DELETE CASCADE, "
        "outcome_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE, PRIMARY KEY(sprint_id,outcome_id))"
    )
    op.execute("CREATE INDEX idx_sprint_outcomes_outcome_id ON sprint_outcomes(outcome_id)")
    op.execute(
        "INSERT INTO sprint_outcomes SELECT sprint_id,id FROM sprint_items WHERE sprint_id IS NOT NULL"
    )
    op.execute(
        "UPDATE scheduled_ticket_schedules SET project_id=(SELECT project_id FROM sprint_items WHERE id=sprint_item_id), "
        "sprint_id=(SELECT sprint_id FROM sprint_items WHERE id=sprint_item_id), "
        "placement_mode=CASE WHEN (SELECT sprint_id FROM sprint_items WHERE id=sprint_item_id) IS NULL "
        "THEN 'backlog' ELSE 'current_sprint' END WHERE placement_mode='sprint_item'"
    )
    with op.batch_alter_table(
        "scheduled_ticket_schedules",
        copy_from=scheduled_ticket_schedules_table(),
        recreate="always",
    ) as batch:
        batch.drop_constraint("ck_scheduled_ticket_placement_mode", type_="check")
        batch.create_check_constraint(
            "ck_scheduled_ticket_placement_mode", "placement_mode IN ('current_sprint','backlog')"
        )
    op.execute("DROP INDEX idx_sprint_items_one_other_per_sprint_project")
    op.execute("ALTER TABLE sprint_items DROP COLUMN sprint_id")
    for table in ("tickets", "agents", "scheduled_ticket_occurrences", "sprint_items"):
        columns = [str(row[1]) for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")]
        names = ",".join(columns)
        # Compare both directions, retaining every column still in the schema.
        for left, right in (
            (table, f"_outcome_before_{table}"),
            (f"_outcome_before_{table}", table),
        ):
            if connection.exec_driver_sql(
                f"SELECT {names} FROM {left} EXCEPT SELECT {names} FROM {right}"
            ).first():
                raise RuntimeError(f"Outcome migration changed preserved {table} records")
    expected = connection.exec_driver_sql(
        "SELECT sprint_id,id FROM _outcome_before_sprint_items WHERE sprint_id IS NOT NULL ORDER BY id"
    ).all()
    actual = connection.exec_driver_sql(
        "SELECT sprint_id,outcome_id FROM sprint_outcomes ORDER BY outcome_id"
    ).all()
    if expected != actual:
        raise RuntimeError("Outcome commitments did not preserve historical placement")
    old_schedules = (
        connection.exec_driver_sql(
            "SELECT * FROM _outcome_before_scheduled_ticket_schedules ORDER BY id"
        )
        .mappings()
        .all()
    )
    for old in old_schedules:
        expected_schedule = dict(old)
        if old["placement_mode"] == "sprint_item":
            item = (
                connection.exec_driver_sql(
                    "SELECT project_id,sprint_id FROM _outcome_before_sprint_items WHERE id=?",
                    (old["sprint_item_id"],),
                )
                .mappings()
                .one()
            )
            expected_schedule.update(
                project_id=item["project_id"],
                sprint_id=item["sprint_id"],
                placement_mode="backlog" if item["sprint_id"] is None else "current_sprint",
            )
        actual_schedule = (
            connection.exec_driver_sql(
                "SELECT * FROM scheduled_ticket_schedules WHERE id=?", (old["id"],)
            )
            .mappings()
            .one()
        )
        if dict(actual_schedule) != expected_schedule:
            raise RuntimeError("Outcome migration changed a schedule destination or template")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise RuntimeError("Outcome migration left a foreign key violation")
    for table in (
        "tickets",
        "sprint_items",
        "agents",
        "scheduled_ticket_schedules",
        "scheduled_ticket_occurrences",
    ):
        op.execute(f"DROP TABLE _outcome_before_{table}")


def downgrade() -> None:
    raise NotImplementedError(
        "multi-Sprint Outcome commitments cannot be reduced to one Sprint without losing intent"
    )
