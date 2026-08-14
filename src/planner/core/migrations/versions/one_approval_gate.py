"""Collapse the two proposal review routes into one approval gate.

A parked proposal is awaiting approval, and that is the whole vocabulary. Both review
routes become the single onward behaviour ``propose``, and both parked statuses become
``awaiting_approval``. No column is added and no value is invented: a Ticket that was on
the agent-review route simply becomes a Ticket awaiting approval.

Revision ID: one_approval_gate
Revises: drop_supervisor_obligations
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

revision = "one_approval_gate"
down_revision = "drop_supervisor_obligations"
branch_labels = None
depends_on = None

_OLD_AT_CAPS = "'stop','agent_review','user_review'"
_NEW_AT_CAPS = "'stop','propose'"
# Both vocabularies are legal while the rows are rewritten between the two rebuilds.
_TRANSITIONAL_AT_CAPS = _OLD_AT_CAPS + ",'propose'"

_OLD_STATUSES = (
    "'empty','blocked','agent','paired','awaiting_agent_review',"
    "'awaiting_user_review','needs_user','user','errored'"
)
_NEW_STATUSES = (
    "'empty','blocked','agent','paired','awaiting_approval','needs_user','user','errored'"
)
_TRANSITIONAL_STATUSES = _OLD_STATUSES + ",'awaiting_approval'"


def _tickets_table(at_caps: str, statuses: str, default_at_cap: str) -> Table:
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
        Column("at_cap", Text, nullable=False, server_default=text(f"'{default_at_cap}'")),
        Column("ticket_status", Text, nullable=False, server_default=text("'empty'")),
        Column("backend_error", Text),
        Column("stage_ownership_overrides", Text, nullable=False, server_default=text("'{}'")),
        Column("default_stage_ownership_mode", Text),
        Column("conversation_id", Text),
        Column("alias", Text),
        Column("fields", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        Column("ticket_status_changed_at", Integer, nullable=False, server_default=text("0")),
        Column("ticket_status_revision", Integer, nullable=False, server_default=text("0")),
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        CheckConstraint(f"at_cap IN ({at_caps})"),
        CheckConstraint(f"ticket_status IN ({statuses})"),
        CheckConstraint("default_stage_ownership_mode IN ('worker','user','paired')"),
        CheckConstraint("ticket_status_revision >= 0"),
    )
    Index("idx_tickets_alias", table.c.alias, unique=True, sqlite_where=text("alias IS NOT NULL"))
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table


def _require_preserved_control_metadata() -> None:
    """A rename of two values must not disturb where a Ticket stands.

    ``ticket_status_changed_at`` and ``ticket_status_revision`` are the freshness and
    identity of a status transition. A rebuild that reset either would make settled
    Tickets look freshly moved to every reader that watches them.
    """
    connection = op.get_bind()
    changed = connection.exec_driver_sql(
        "SELECT 1 FROM _one_approval_gate_before b JOIN tickets t ON t.id=b.id "
        "WHERE t.stage IS NOT b.stage OR t.ceiling IS NOT b.ceiling OR "
        "t.ticket_status_changed_at IS NOT b.ticket_status_changed_at OR "
        "t.ticket_status_revision IS NOT b.ticket_status_revision OR "
        "t.conversation_id IS NOT b.conversation_id OR "
        "t.fields IS NOT b.fields OR "
        "t.stage_ownership_overrides IS NOT b.stage_ownership_overrides OR "
        "t.default_stage_ownership_mode IS NOT b.default_stage_ownership_mode LIMIT 1"
    ).first()
    count_changed = connection.exec_driver_sql(
        "SELECT 1 WHERE (SELECT count(*) FROM tickets) != "
        "(SELECT count(*) FROM _one_approval_gate_before)"
    ).first()
    if changed is not None or count_changed is not None:
        raise RuntimeError("One approval gate migration changed control metadata")


def upgrade() -> None:
    op.execute(
        "CREATE TEMP TABLE _one_approval_gate_before AS SELECT id, stage, ceiling, "
        "ticket_status_changed_at, ticket_status_revision, conversation_id, fields, "
        "stage_ownership_overrides, default_stage_ownership_mode FROM tickets"
    )
    with op.batch_alter_table(
        "tickets",
        copy_from=_tickets_table(_TRANSITIONAL_AT_CAPS, _TRANSITIONAL_STATUSES, "user_review"),
        recreate="always",
    ):
        pass

    op.execute("UPDATE tickets SET at_cap='propose' WHERE at_cap IN ('user_review','agent_review')")
    op.execute(
        "UPDATE tickets SET ticket_status='awaiting_approval' "
        "WHERE ticket_status IN ('awaiting_agent_review','awaiting_user_review')"
    )

    with op.batch_alter_table(
        "tickets",
        copy_from=_tickets_table(_NEW_AT_CAPS, _NEW_STATUSES, "propose"),
        recreate="always",
    ):
        pass
    _require_preserved_control_metadata()
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"One approval gate migration failed: {violations!r}")
    op.execute("DROP TABLE _one_approval_gate_before")


def downgrade() -> None:
    raise NotImplementedError("the two review routes cannot be told apart once collapsed")
