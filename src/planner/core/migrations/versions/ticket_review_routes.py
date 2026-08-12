"""Split parked Ticket proposals into agent-review and user-review routes.

Revision ID: ticket_review_routes
Revises: sprint_item_supervisors
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

revision = "ticket_review_routes"
down_revision = "sprint_item_supervisors"
branch_labels = None
depends_on = None

_FINAL_ROUTES = "'stop','agent_review','user_review'"
_TRANSITIONAL_ROUTES = _FINAL_ROUTES + ",'propose'"
_FINAL_STATUSES = (
    "'empty','blocked','agent','paired','awaiting_agent_review',"
    "'awaiting_user_review','needs_user','user','errored'"
)
_TRANSITIONAL_STATUSES = _FINAL_STATUSES + ",'awaiting_approval'"


def _tickets_table(routes: str, statuses: str, default_route: str) -> Table:
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
        Column("at_cap", Text, nullable=False, server_default=text(f"'{default_route}'")),
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
        CheckConstraint(f"at_cap IN ({routes})"),
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
    connection = op.get_bind()
    changed = connection.exec_driver_sql(
        "SELECT 1 FROM _ticket_review_routes_before b JOIN tickets t ON t.id=b.id "
        "WHERE t.stage IS NOT b.stage OR t.ceiling IS NOT b.ceiling OR "
        "t.ticket_status_changed_at IS NOT b.ticket_status_changed_at OR "
        "t.ticket_status_revision IS NOT b.ticket_status_revision OR "
        "t.conversation_id IS NOT b.conversation_id OR "
        "t.stage_ownership_overrides IS NOT b.stage_ownership_overrides OR "
        "t.default_stage_ownership_mode IS NOT b.default_stage_ownership_mode LIMIT 1"
    ).first()
    count_changed = connection.exec_driver_sql(
        "SELECT 1 WHERE (SELECT count(*) FROM tickets) != "
        "(SELECT count(*) FROM _ticket_review_routes_before)"
    ).first()
    if changed is not None or count_changed is not None:
        raise RuntimeError("Ticket review route migration changed control metadata")


def upgrade() -> None:
    op.execute(
        "CREATE TEMP TABLE _ticket_review_routes_before AS SELECT id, stage, ceiling, "
        "ticket_status_changed_at, ticket_status_revision, conversation_id, "
        "stage_ownership_overrides, default_stage_ownership_mode FROM tickets"
    )
    with op.batch_alter_table(
        "tickets",
        copy_from=_tickets_table(_TRANSITIONAL_ROUTES, _TRANSITIONAL_STATUSES, "propose"),
        recreate="always",
    ):
        pass

    op.execute("UPDATE tickets SET at_cap='user_review' WHERE at_cap='propose'")
    op.execute(
        "UPDATE tickets SET ticket_status='awaiting_user_review' "
        "WHERE ticket_status='awaiting_approval'"
    )
    # Rebuild each field slot while preserving every proposal property and adding its
    # compatibility route. The correlated json_each supports every registered Worker type.
    op.execute(
        "UPDATE tickets SET fields=(SELECT json_group_object(j.key, "
        "CASE WHEN json_type(j.value, '$.proposal')='object' "
        "THEN json_set(j.value, '$.proposal.review_route', 'user_review') ELSE j.value END) "
        "FROM json_each(tickets.fields) AS j)"
    )

    with op.batch_alter_table(
        "tickets",
        copy_from=_tickets_table(_FINAL_ROUTES, _FINAL_STATUSES, "user_review"),
        recreate="always",
    ):
        pass
    _require_preserved_control_metadata()
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Ticket review route migration failed: {violations!r}")
    op.execute("DROP TABLE _ticket_review_routes_before")


def downgrade() -> None:
    raise NotImplementedError("agent-review routing cannot be collapsed without losing intent")
