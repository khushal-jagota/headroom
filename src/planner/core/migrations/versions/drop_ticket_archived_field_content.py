"""Drop the record of unapproved Ticket drafts.

A proposal has two outcomes: it is approved, or it is sent back with guidance for the
agent that wrote it. Neither leaves a withdrawn draft to keep, and there is no longer an
editor that produced one, so the column has no writer and no reader left.

Dropping a Ticket no longer keeps a copy of whatever proposal was still parked on it.

The rebuild is the same one the revision before this used, and for the same reason: the
table declares CHECK constraints, so it is recreated rather than altered in place.

Revision ID: drop_ticket_archived_field_content
Revises: drop_ticket_at_cap
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

revision = "drop_ticket_archived_field_content"
down_revision = "drop_ticket_at_cap"
branch_labels = None
depends_on = None

_RETAINED_COLUMNS = (
    "id,title,worker_type,employee_backend,employee_launch_model,"
    "employee_launch_reasoning_effort,stage,priority,deadline,project_id,"
    "sprint_item_id,recap,ceiling,ticket_status,conversation_id,field_values,"
    "created_at,updated_at,ticket_status_changed_at,ticket_status_revision,sprint_id,"
    "guidance,pending_proposal,ceiling_holder"
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
        f"SELECT {_RETAINED_COLUMNS} FROM _tickets_before_archived_field_content_drop EXCEPT "
        f"SELECT {_RETAINED_COLUMNS} FROM tickets LIMIT 1"
    ).first()
    added = connection.exec_driver_sql(
        f"SELECT {_RETAINED_COLUMNS} FROM tickets EXCEPT "
        f"SELECT {_RETAINED_COLUMNS} FROM _tickets_before_archived_field_content_drop LIMIT 1"
    ).first()
    if changed is not None or added is not None:
        raise RuntimeError("dropping archived_field_content changed retained Ticket data")


def upgrade() -> None:
    op.execute(
        f"CREATE TEMP TABLE _tickets_before_archived_field_content_drop AS SELECT {_RETAINED_COLUMNS} FROM tickets"
    )
    with op.batch_alter_table("tickets", copy_from=_tickets_table(), recreate="always") as batch_op:
        batch_op.drop_column("archived_field_content")
    _require_retained_rows_unchanged()
    op.execute("DROP TABLE _tickets_before_archived_field_content_drop")
    remaining = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT count(*) FROM pragma_table_info('tickets') WHERE name = 'archived_field_content'"
        )
        .scalar()
    )
    if remaining:
        raise RuntimeError("the archived_field_content column survived the rebuild")


def downgrade() -> None:
    raise NotImplementedError("withdrawn drafts cannot be recovered once discarded")
