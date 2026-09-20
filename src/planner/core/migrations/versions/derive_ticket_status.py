"""Stop storing a Ticket's status. Keep only the worker-step claim.

Four of the five statuses were questions about facts stored elsewhere: a parked proposal
answers ``awaiting_approval``, an active blocking Ticket answers ``blocked``, and the
absence of both answers ``empty``. Only ``agent`` and ``errored`` said something the rest
of the database did not already know — that the wakeup system has sent this Ticket's
worker its step, and whether that ended in failure. That is the claim, and it is all that
stays.

``ticket_status_changed_at`` and ``ticket_status_revision`` were always the freshness and
the identity of a claim transition, so they come across under the claim's name with their
values untouched. A settled Ticket must not look freshly moved.

Before anything is rewritten, this revision reports every Ticket whose derived status
differs from its stored one. Those rows are the drift the duplication allowed, and
naming them is the point of doing this once rather than repairing it forever.

Revision ID: derive_ticket_status
Revises: drop_ticket_archived_field_content
"""

from __future__ import annotations

import logging
from typing import Any

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

revision = "derive_ticket_status"
down_revision = "drop_ticket_archived_field_content"
branch_labels = None
depends_on = None

_LOG = logging.getLogger("alembic.runtime.migration")

_CLAIMS = "'none','out','errored'"

# The status a reader would derive, written in SQL so the report runs before any rewrite.
_DERIVED_STATUS = (
    "CASE WHEN t.ticket_status = 'errored' THEN 'errored' "
    "WHEN t.pending_proposal IS NOT NULL THEN 'awaiting_approval' "
    "WHEN t.ticket_status = 'agent' THEN 'agent' "
    "WHEN EXISTS (SELECT 1 FROM ticket_blocks b JOIN tickets blocker "
    "ON blocker.id = b.blocking_ticket_id WHERE b.blocked_ticket_id = t.id "
    "AND blocker.stage NOT IN ('done','dropped')) THEN 'blocked' "
    "ELSE 'empty' END"
)


def _ceiling_holder_check() -> str:
    return (
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
    )


def _tickets_table(*, with_status: bool, with_claim: bool, status_checks: bool = True) -> Table:
    """The table shape at one point in the rewrite.

    Both column sets are legal in the middle, while the claim is filled from the status.
    """
    columns: list[Column[Any] | CheckConstraint] = [
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
        Column("conversation_id", Text),
        Column("field_values", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        Column("guidance", Text, nullable=False, server_default=text("''")),
        Column("pending_proposal", Text),
        Column(
            "ceiling_holder",
            Text,
            nullable=False,
            server_default=text('\'{"id":"owner","kind":"owner"}\''),
        ),
    ]
    if with_status:
        columns += [
            Column("ticket_status", Text, nullable=False, server_default=text("'empty'")),
            Column("ticket_status_changed_at", Integer, nullable=False, server_default=text("0")),
            Column("ticket_status_revision", Integer, nullable=False, server_default=text("0")),
        ]
        # The status CHECKs are deliberately absent from the shape the final rebuild
        # copies: the rebuilt table is declared without them, which is how a constraint
        # leaves with its column.
        if status_checks:
            columns += [
                CheckConstraint(
                    "ticket_status IN ('empty','blocked','agent','awaiting_approval','errored')"
                ),
                CheckConstraint("ticket_status_revision >= 0"),
            ]
    if with_claim:
        columns += [
            Column("worker_step_claim", Text, nullable=False, server_default=text("'none'")),
            Column(
                "worker_step_claim_changed_at", Integer, nullable=False, server_default=text("0")
            ),
            Column("worker_step_claim_revision", Integer, nullable=False, server_default=text("0")),
            CheckConstraint(f"worker_step_claim IN ({_CLAIMS})"),
            CheckConstraint("worker_step_claim_revision >= 0"),
        ]
    columns += [
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        CheckConstraint(_ceiling_holder_check()),
    ]
    table = Table("tickets", MetaData(), *columns)
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table


def report_status_disagreements() -> list[tuple[str, str, str]]:
    """Every Ticket whose stored status disagrees with the status a reader would derive.

    Returned as ``(ticket_id, stored, derived)`` and logged. These are the rows the
    duplication allowed to drift, and they are the reason the stored value goes.
    """
    rows = [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in op.get_bind()
        .exec_driver_sql(
            f"SELECT t.id, t.ticket_status, {_DERIVED_STATUS} AS derived FROM tickets t "
            f"WHERE t.ticket_status != ({_DERIVED_STATUS}) ORDER BY t.id"
        )
        .fetchall()
    ]
    if not rows:
        _LOG.info("derive_ticket_status: no Ticket disagreed with its derived status")
    for ticket_id, stored, derived in rows:
        _LOG.info(
            "derive_ticket_status: %s stored=%s derived=%s",
            ticket_id,
            stored,
            derived,
        )
    return rows


def _require_claim_carried_the_transition() -> None:
    """The claim must inherit the status transition's freshness and identity exactly."""
    connection = op.get_bind()
    changed = connection.exec_driver_sql(
        "SELECT 1 FROM _tickets_before_derive_status b JOIN tickets t ON t.id = b.id "
        "WHERE t.worker_step_claim_changed_at IS NOT b.ticket_status_changed_at "
        "OR t.worker_step_claim_revision IS NOT b.ticket_status_revision "
        "OR t.stage IS NOT b.stage OR t.ceiling IS NOT b.ceiling "
        "OR t.pending_proposal IS NOT b.pending_proposal "
        "OR t.field_values IS NOT b.field_values LIMIT 1"
    ).first()
    count_changed = connection.exec_driver_sql(
        "SELECT 1 WHERE (SELECT count(*) FROM tickets) != "
        "(SELECT count(*) FROM _tickets_before_derive_status)"
    ).first()
    if changed is not None or count_changed is not None:
        raise RuntimeError("deriving the Ticket status changed control metadata")


def upgrade() -> None:
    report_status_disagreements()
    op.execute(
        "CREATE TEMP TABLE _tickets_before_derive_status AS SELECT id, stage, ceiling, "
        "ticket_status, ticket_status_changed_at, ticket_status_revision, pending_proposal, "
        "field_values FROM tickets"
    )
    with op.batch_alter_table(
        "tickets",
        copy_from=_tickets_table(with_status=True, with_claim=False),
        recreate="always",
    ) as batch_op:
        batch_op.add_column(
            Column("worker_step_claim", Text, nullable=False, server_default=text("'none'"))
        )
        batch_op.add_column(
            Column(
                "worker_step_claim_changed_at", Integer, nullable=False, server_default=text("0")
            )
        )
        batch_op.add_column(
            Column("worker_step_claim_revision", Integer, nullable=False, server_default=text("0"))
        )

    op.execute(
        "UPDATE tickets SET worker_step_claim = CASE ticket_status "
        "WHEN 'agent' THEN 'out' WHEN 'errored' THEN 'errored' ELSE 'none' END, "
        "worker_step_claim_changed_at = ticket_status_changed_at, "
        "worker_step_claim_revision = ticket_status_revision"
    )

    with op.batch_alter_table(
        "tickets",
        copy_from=_tickets_table(with_status=True, with_claim=True, status_checks=False),
        recreate="always",
    ) as batch_op:
        batch_op.drop_column("ticket_status")
        batch_op.drop_column("ticket_status_changed_at")
        batch_op.drop_column("ticket_status_revision")

    _require_claim_carried_the_transition()
    op.execute("DROP TABLE _tickets_before_derive_status")

    remaining = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT count(*) FROM pragma_table_info('tickets') "
            "WHERE name IN ('ticket_status','ticket_status_changed_at','ticket_status_revision')"
        )
        .scalar()
    )
    if remaining:
        raise RuntimeError("a stored status column survived the rebuild")
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"deriving the Ticket status failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("a derived status has nowhere to be stored back into")
