"""Derive owner attention and retire human-control Ticket statuses.

Revision ID: work_attention
Revises: proposal_holder_wakes
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

revision = "work_attention"
down_revision = "proposal_holder_wakes"
branch_labels = None
depends_on = None


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
        Column("backend_error", Text),
        Column("stage_ownership_overrides", Text, nullable=False, server_default=text("'{}'")),
        Column("default_stage_ownership_mode", Text),
        Column("conversation_id", Text),
        Column("alias", Text),
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
        CheckConstraint("default_stage_ownership_mode IN ('worker','user','paired')"),
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
    Index("idx_tickets_alias", table.c.alias, unique=True, sqlite_where=text("alias IS NOT NULL"))
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table


def _migrate_legacy_help_messages() -> None:
    op.execute(
        "INSERT INTO conversation_events(conversation_id, sequence, kind, payload, created_at) "
        "SELECT c.conversation_id, c.latest_sequence + 1, 'message_to_owner', "
        "json_object("
        "'text', 'This worker requested help before addressed help messages were available.', "
        "'sender_label', 'Ticket ' || t.id, "
        "'sender', json_object('kind','ticket','id',t.id), "
        "'recipient', json_object('kind','owner','id','owner'), "
        "'sender_message_id', 'legacy-help-' || t.id), "
        "t.ticket_status_changed_at "
        "FROM tickets t JOIN conversations c ON c.conversation_id = t.conversation_id "
        "WHERE t.ticket_status = 'needs_user' "
        "AND json_extract(t.ceiling_holder, '$.kind') = 'owner'"
    )
    op.execute(
        "UPDATE conversations SET latest_sequence = latest_sequence + 1 "
        "WHERE conversation_id IN ("
        "SELECT conversation_id FROM tickets WHERE ticket_status = 'needs_user' "
        "AND json_extract(ceiling_holder, '$.kind') = 'owner'"
        ")"
    )


def _rebuild_notification_preferences() -> None:
    op.execute("ALTER TABLE notification_preferences RENAME TO _old_notification_preferences")
    op.execute(
        "CREATE TABLE notification_preferences ("
        "subject_key TEXT NOT NULL CHECK (subject_key IN "
        "('tickets','chief_of_staff','sprint_item_supervisors')),"
        "notification_type TEXT NOT NULL CHECK (notification_type IN "
        "('awaiting_reply','awaiting_approval','assigned','errored')),"
        "enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),"
        "updated_at INTEGER NOT NULL, PRIMARY KEY(subject_key, notification_type))"
    )
    op.execute(
        "INSERT INTO notification_preferences(subject_key, notification_type, enabled, updated_at) "
        "SELECT subject_key, 'awaiting_reply', MAX(enabled), MAX(updated_at) "
        "FROM _old_notification_preferences WHERE notification_type IN "
        "('needs_input','permission_requested') GROUP BY subject_key"
    )
    op.execute(
        "INSERT INTO notification_preferences(subject_key, notification_type, enabled, updated_at) "
        "SELECT subject_key, 'awaiting_approval', enabled, updated_at "
        "FROM _old_notification_preferences WHERE subject_key = 'tickets' "
        "AND notification_type = 'ticket_needs_approval'"
    )
    op.execute(
        "INSERT INTO notification_preferences(subject_key, notification_type, enabled, updated_at) "
        "SELECT subject_key, 'errored', enabled, updated_at "
        "FROM _old_notification_preferences WHERE notification_type = 'worker_failed'"
    )
    op.execute("DROP TABLE _old_notification_preferences")


def upgrade() -> None:
    _migrate_legacy_help_messages()
    op.execute(
        "CREATE TEMP TABLE _paired_openers_to_migrate AS "
        "SELECT id AS ticket_id, stage, ticket_status_changed_at AS opened_at "
        "FROM tickets WHERE ticket_status = 'paired' OR (ticket_status = 'needs_user' AND "
        "COALESCE(json_extract(stage_ownership_overrides, '$.' || stage), "
        "default_stage_ownership_mode) = 'paired')"
    )
    op.execute(
        "UPDATE tickets SET ticket_status = CASE ticket_status "
        "WHEN 'user' THEN 'empty' WHEN 'paired' THEN 'empty' "
        "WHEN 'needs_user' THEN CASE WHEN COALESCE("
        "json_extract(stage_ownership_overrides, '$.' || stage), "
        "default_stage_ownership_mode) IN ('user','paired') "
        "THEN 'empty' ELSE 'agent' END ELSE ticket_status END"
    )
    with op.batch_alter_table("tickets", copy_from=_tickets_table(), recreate="always"):
        pass
    op.execute(
        "CREATE TABLE ticket_paired_stage_openers ("
        "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,"
        "stage TEXT NOT NULL, opened_at INTEGER NOT NULL)"
    )
    op.execute(
        "INSERT INTO ticket_paired_stage_openers(ticket_id, stage, opened_at) "
        "SELECT ticket_id, stage, opened_at FROM _paired_openers_to_migrate"
    )
    op.execute("DROP TABLE _paired_openers_to_migrate")
    op.execute(
        "CREATE TRIGGER clear_ticket_paired_stage_opener_after_stage_change "
        "AFTER UPDATE OF stage ON tickets WHEN OLD.stage != NEW.stage BEGIN "
        "DELETE FROM ticket_paired_stage_openers WHERE ticket_id = NEW.id; END"
    )
    _rebuild_notification_preferences()
    op.execute(
        "DELETE FROM notification_facts WHERE fact_id NOT IN "
        "(SELECT fact_id FROM notification_decisions)"
    )
    op.execute(
        "CREATE TABLE notification_assignment_state ("
        "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,"
        "assigned INTEGER NOT NULL CHECK (assigned IN (0,1)),"
        "generation INTEGER NOT NULL CHECK (generation >= 0))"
    )
    op.execute(
        "INSERT INTO notification_assignment_state(ticket_id, assigned, generation) "
        "SELECT id, CASE WHEN COALESCE("
        "json_extract(stage_ownership_overrides, '$.' || stage),"
        "default_stage_ownership_mode) IN ('user','paired') "
        "OR (stage = 'needs_kickoff' AND json_extract(ceiling_holder, '$.kind') = 'owner') "
        "THEN 1 ELSE 0 END, 0 FROM tickets"
    )
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"work-attention migration failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("retired attention statuses cannot be reconstructed")
