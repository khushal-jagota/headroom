"""Make Worker type definitions the only source of Stage ownership.

Revision ID: two_ownership_modes
Revises: remove_ticket_alias_and_backend_error
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

revision = "two_ownership_modes"
down_revision = "remove_ticket_alias_and_backend_error"
branch_labels = None
depends_on = None


# This is a migration input, not a live registry lookup. It freezes every shipped Worker
# type and Stage at the cutover. Stages formerly declared as paired are user-owned here.
_SHIPPED_STAGE_OWNERSHIP = (
    ("coding", "needs_kickoff", "worker"),
    ("coding", "needs_success", "worker"),
    ("coding", "needs_approach", "worker"),
    ("coding", "needs_plan", "worker"),
    ("coding", "needs_implementation", "worker"),
    ("coding", "needs_closeout", "worker"),
    ("coding", "done", "worker"),
    ("coding", "dropped", "worker"),
    ("general", "needs_kickoff", "worker"),
    ("general", "needs_execution", "worker"),
    ("general", "needs_closeout", "worker"),
    ("general", "done", "worker"),
    ("general", "dropped", "worker"),
    ("debugging", "needs_kickoff", "worker"),
    ("debugging", "needs_problem_understanding", "worker"),
    ("debugging", "needs_structural_diagnosis", "worker"),
    ("debugging", "needs_solution", "worker"),
    ("debugging", "needs_closeout", "worker"),
    ("debugging", "done", "worker"),
    ("debugging", "dropped", "worker"),
    ("new_worker", "needs_kickoff", "worker"),
    ("new_worker", "needs_understanding", "user"),
    ("new_worker", "needs_stages", "worker"),
    ("new_worker", "needs_thinking", "worker"),
    ("new_worker", "needs_runtime_defaults", "user"),
    ("new_worker", "needs_drafting", "worker"),
    ("new_worker", "needs_closeout", "worker"),
    ("new_worker", "done", "worker"),
    ("new_worker", "dropped", "worker"),
    ("amend_worker", "needs_kickoff", "worker"),
    ("amend_worker", "needs_amendment", "user"),
    ("amend_worker", "needs_drafting", "worker"),
    ("amend_worker", "needs_closeout", "worker"),
    ("amend_worker", "done", "worker"),
    ("amend_worker", "dropped", "worker"),
    ("exploration", "needs_kickoff", "worker"),
    ("exploration", "needs_understanding", "user"),
    ("exploration", "needs_research_plan", "worker"),
    ("exploration", "needs_research", "worker"),
    ("exploration", "needs_answer", "user"),
    ("exploration", "needs_follow_up", "worker"),
    ("exploration", "needs_closeout", "worker"),
    ("exploration", "done", "worker"),
    ("exploration", "dropped", "worker"),
    ("initiative_planning", "needs_kickoff", "worker"),
    ("initiative_planning", "needs_rough_shape", "worker"),
    ("initiative_planning", "needs_question_tree", "worker"),
    ("initiative_planning", "needs_question_answers", "user"),
    ("initiative_planning", "needs_ticket_outlines", "worker"),
    ("initiative_planning", "needs_closeout", "worker"),
    ("initiative_planning", "done", "worker"),
    ("initiative_planning", "dropped", "worker"),
    ("initiative_review", "needs_kickoff", "worker"),
    ("initiative_review", "needs_review", "worker"),
    ("initiative_review", "needs_feedback", "user"),
    ("initiative_review", "needs_followups", "worker"),
    ("initiative_review", "needs_closeout", "worker"),
    ("initiative_review", "done", "worker"),
    ("initiative_review", "dropped", "worker"),
    ("product_design", "needs_kickoff", "worker"),
    ("product_design", "needs_direction", "worker"),
    ("product_design", "needs_wireframe", "user"),
    ("product_design", "needs_design", "user"),
    ("product_design", "needs_closeout", "worker"),
    ("product_design", "done", "worker"),
    ("product_design", "dropped", "worker"),
    ("planning-day", "needs_review", "worker"),
    ("planning-day", "needs_direction", "user"),
    ("planning-day", "needs_day_changes", "worker"),
    ("planning-day", "needs_closeout", "worker"),
    ("planning-day", "done", "worker"),
    ("planning-day", "dropped", "worker"),
    ("planning-midday-check", "needs_kickoff", "worker"),
    ("planning-midday-check", "needs_action", "worker"),
    ("planning-midday-check", "needs_closeout", "worker"),
    ("planning-midday-check", "done", "worker"),
    ("planning-midday-check", "dropped", "worker"),
    ("planning-sprint", "needs_kickoff", "worker"),
    ("planning-sprint", "needs_review", "worker"),
    ("planning-sprint", "needs_next_sprint", "worker"),
    ("planning-sprint", "needs_closeout", "worker"),
    ("planning-sprint", "done", "worker"),
    ("planning-sprint", "dropped", "worker"),
    ("personal", "needs_kickoff", "user"),
    ("personal", "needs_outcome", "user"),
    ("personal", "needs_closeout", "worker"),
    ("personal", "done", "worker"),
    ("personal", "dropped", "worker"),
    ("research", "needs_kickoff", "worker"),
    ("research", "needs_research_plan", "worker"),
    ("research", "needs_research", "worker"),
    ("research", "needs_closeout", "worker"),
    ("research", "done", "worker"),
    ("research", "dropped", "worker"),
)


def _tickets_table() -> Table:
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
        CheckConstraint("at_cap IN ('stop','propose')"),
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


def _install_frozen_ownership_map() -> None:
    op.execute(
        "CREATE TEMP TABLE _shipped_stage_ownership ("
        "worker_type TEXT NOT NULL, stage TEXT NOT NULL, "
        "ownership_mode TEXT NOT NULL CHECK (ownership_mode IN ('worker','user')), "
        "PRIMARY KEY(worker_type,stage))"
    )
    connection = op.get_bind()
    connection.exec_driver_sql(
        "INSERT INTO _shipped_stage_ownership(worker_type,stage,ownership_mode) VALUES (?,?,?)",
        list(_SHIPPED_STAGE_OWNERSHIP),
    )


def upgrade() -> None:
    connection = op.get_bind()
    _install_frozen_ownership_map()
    unknown = connection.exec_driver_sql(
        "SELECT t.id,t.worker_type,t.stage FROM tickets t "
        "LEFT JOIN _shipped_stage_ownership o "
        "ON o.worker_type=t.worker_type AND o.stage=t.stage "
        "WHERE o.worker_type IS NULL ORDER BY t.id LIMIT 1"
    ).first()
    if unknown is not None:
        raise RuntimeError(
            f"two-ownership migration found an undeclared Ticket Stage: {tuple(unknown)!r}"
        )

    preserved_columns = [
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(tickets)")
        if str(row[1]) not in {"stage_ownership_overrides", "default_stage_ownership_mode"}
    ]
    preserved_names = ",".join(preserved_columns)
    op.execute(
        f"CREATE TEMP TABLE _two_ownership_modes_before_tickets AS "
        f"SELECT {preserved_names} FROM tickets"
    )

    # Existing overrides can point in either direction. Assigned attention now follows
    # only the shipped declaration, while the kickoff assignment rule remains unchanged.
    op.execute(
        "INSERT OR IGNORE INTO notification_attention_state"
        "(subject_kind,subject_id,notification_type,active,generation) "
        "SELECT 'ticket',id,'assigned',0,0 FROM tickets"
    )
    op.execute(
        "UPDATE notification_attention_state AS attention SET active = CASE WHEN EXISTS ("
        "SELECT 1 FROM tickets t JOIN _shipped_stage_ownership o "
        "ON o.worker_type=t.worker_type AND o.stage=t.stage WHERE t.id=attention.subject_id "
        "AND (o.ownership_mode='user' OR (t.stage='needs_kickoff' "
        "AND json_extract(t.ceiling_holder, '$.kind')='owner'))) THEN 1 ELSE 0 END "
        "WHERE attention.subject_kind='ticket' AND attention.notification_type='assigned'"
    )

    with op.batch_alter_table("tickets", copy_from=_tickets_table(), recreate="always"):
        pass

    for left, right in (
        ("tickets", "_two_ownership_modes_before_tickets"),
        ("_two_ownership_modes_before_tickets", "tickets"),
    ):
        changed = connection.exec_driver_sql(
            f"SELECT {preserved_names} FROM {left} "
            f"EXCEPT SELECT {preserved_names} FROM {right} LIMIT 1"
        ).first()
        if changed is not None:
            raise RuntimeError("two-ownership migration changed preserved Ticket data")

    wrong_attention = connection.exec_driver_sql(
        "SELECT t.id FROM tickets t JOIN _shipped_stage_ownership o "
        "ON o.worker_type=t.worker_type AND o.stage=t.stage "
        "LEFT JOIN notification_attention_state attention "
        "ON attention.subject_kind='ticket' AND attention.subject_id=t.id "
        "AND attention.notification_type='assigned' "
        "WHERE attention.subject_id IS NULL OR attention.active != CASE WHEN "
        "o.ownership_mode='user' OR (t.stage='needs_kickoff' "
        "AND json_extract(t.ceiling_holder, '$.kind')='owner') THEN 1 ELSE 0 END LIMIT 1"
    ).first()
    if wrong_attention is not None:
        raise RuntimeError("two-ownership migration did not reconcile assigned attention")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"two-ownership migration left foreign key violations: {violations!r}")
    op.execute("DROP TABLE _two_ownership_modes_before_tickets")
    op.execute("DROP TABLE _shipped_stage_ownership")


def downgrade() -> None:
    raise NotImplementedError("Ticket-specific Stage ownership cannot be reconstructed")
