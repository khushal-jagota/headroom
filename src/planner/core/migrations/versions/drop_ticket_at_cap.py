"""Drop the second half of a Ticket's scope.

A ceiling names the last thing a worker is allowed to do. The worker does that thing,
proposes it, and waits to be approved. There is no second setting deciding whether it
may propose at all, so the column has no reader left.

Every Ticket that carried ``stop`` now behaves the way ``propose`` did: on reaching its
ceiling it takes one more step, files a proposal, and parks for the user.

The column carries a table CHECK, so SQLite refuses a plain DROP COLUMN and the table is
rebuilt instead, the same way earlier revisions rebuilt it.

Revision ID: drop_ticket_at_cap
Revises: worker_types_in_database
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

revision = "drop_ticket_at_cap"
down_revision = "worker_types_in_database"
branch_labels = None
depends_on = None

_RETAINED_COLUMNS = (
    "id,title,worker_type,employee_backend,employee_launch_model,"
    "employee_launch_reasoning_effort,stage,priority,deadline,project_id,"
    "sprint_item_id,recap,ceiling,ticket_status,conversation_id,field_values,"
    "created_at,updated_at,ticket_status_changed_at,ticket_status_revision,sprint_id,"
    "guidance,pending_proposal,archived_field_content,ceiling_holder"
)


def _tickets_table() -> Table:
    """The complete table shape this revision inherits, before the column goes."""
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
        Column("recap", Text, nullable=False, server_default=text("''")),
        Column("ceiling", Text, nullable=False),
        Column("at_cap", Text, nullable=False, server_default=text("'propose'")),
        Column("ticket_status", Text, nullable=False, server_default=text("'empty'")),
        Column("conversation_id", Text),
        Column("field_values", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        Column("ticket_status_changed_at", Integer, nullable=False, server_default=text("0")),
        Column("ticket_status_revision", Integer, nullable=False, server_default=text("0")),
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        Column("guidance", Text, nullable=False, server_default=text("''")),
        Column("pending_proposal", Text),
        Column("archived_field_content", Text, nullable=False, server_default=text("''")),
        Column(
            "ceiling_holder",
            Text,
            nullable=False,
            server_default=text('\'{"id":"owner","kind":"owner"}\''),
        ),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        # The cap's CHECK is deliberately absent: the rebuilt table is declared without
        # it, which is how the constraint leaves with the column.
        CheckConstraint(
            "ticket_status IN ('empty','blocked','agent','awaiting_approval','errored')"
        ),
        CheckConstraint("ticket_status_revision >= 0"),
        CheckConstraint(
            "COALESCE(json_type(ceiling_holder) = 'object' "
            "AND json_type(ceiling_holder, '$.kind') = 'text' "
            "AND json_type(ceiling_holder, '$.id') = 'text' "
            "AND json_extract(ceiling_holder, '$.kind') "
            "IN ('owner','chief','sprint_item','ticket') "
            "AND length(trim(json_extract(ceiling_holder, '$.id'))) > 0 "
            "AND json_extract(ceiling_holder, '$.id') = "
            "trim(json_extract(ceiling_holder, '$.id')) "
            "AND (json_extract(ceiling_holder, '$.kind') != 'owner' "
            "OR json_extract(ceiling_holder, '$.id') = 'owner') "
            "AND (json_extract(ceiling_holder, '$.kind') != 'chief' "
            "OR json_extract(ceiling_holder, '$.id') = 'chief'), 0)"
        ),
    )
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table


def _require_retained_rows_unchanged() -> None:
    connection = op.get_bind()
    changed = connection.exec_driver_sql(
        f"SELECT {_RETAINED_COLUMNS} FROM _tickets_before_at_cap_drop EXCEPT "
        f"SELECT {_RETAINED_COLUMNS} FROM tickets LIMIT 1"
    ).first()
    added = connection.exec_driver_sql(
        f"SELECT {_RETAINED_COLUMNS} FROM tickets EXCEPT "
        f"SELECT {_RETAINED_COLUMNS} FROM _tickets_before_at_cap_drop LIMIT 1"
    ).first()
    if changed is not None or added is not None:
        raise RuntimeError("dropping at_cap changed retained Ticket data")


def upgrade() -> None:
    op.execute(
        f"CREATE TEMP TABLE _tickets_before_at_cap_drop AS SELECT {_RETAINED_COLUMNS} FROM tickets"
    )
    with op.batch_alter_table("tickets", copy_from=_tickets_table(), recreate="always") as batch_op:
        batch_op.drop_column("at_cap")
    _require_retained_rows_unchanged()
    op.execute("DROP TABLE _tickets_before_at_cap_drop")
    remaining = (
        op.get_bind()
        .exec_driver_sql("SELECT count(*) FROM pragma_table_info('tickets') WHERE name = 'at_cap'")
        .scalar()
    )
    if remaining:
        raise RuntimeError("the at_cap column survived the rebuild")


def downgrade() -> None:
    raise NotImplementedError("a cap nobody sets again cannot be recovered")
