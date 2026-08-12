"""Store durable work and delivery attempts for Sprint Item supervisors.

Revision ID: supervisor_obligations
Revises: project_folder_path
"""

from __future__ import annotations

from alembic import op

revision = "supervisor_obligations"
down_revision = "project_folder_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE supervisor_obligation_deliveries ("
        "id TEXT PRIMARY KEY,"
        "sprint_item_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE,"
        "sender_message_id TEXT NOT NULL UNIQUE,"
        "conversation_id TEXT,"
        "state TEXT NOT NULL CHECK(state IN "
        "('prepared','dispatching','queued','delivered','refused','uncertain','failed')),"
        "error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
    )
    op.execute(
        "CREATE TABLE supervisor_obligations ("
        "id TEXT PRIMARY KEY,"
        "sprint_item_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE,"
        "ticket_id TEXT NOT NULL,"
        "kind TEXT NOT NULL CHECK(kind IN "
        "('agent_review','user_review','blocked','worker_failure','needs_user','completed')),"
        "source_identity TEXT NOT NULL,"
        "lifecycle TEXT NOT NULL CHECK(lifecycle IN "
        "('pending','delivered','acknowledged','resolved','superseded','failed')),"
        "delivery_id TEXT REFERENCES supervisor_obligation_deliveries(id) ON DELETE SET NULL,"
        "attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),"
        "retry_at INTEGER,last_error TEXT,acknowledged_at INTEGER,"
        "created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
    )
    op.execute(
        "CREATE TABLE supervisor_obligation_delivery_members ("
        "delivery_id TEXT NOT NULL REFERENCES supervisor_obligation_deliveries(id) ON DELETE CASCADE,"
        "obligation_id TEXT NOT NULL REFERENCES supervisor_obligations(id) ON DELETE CASCADE,"
        "ordinal INTEGER NOT NULL CHECK(ordinal>=0),"
        "PRIMARY KEY(delivery_id,obligation_id),UNIQUE(delivery_id,ordinal))"
    )
    op.execute(
        "CREATE INDEX idx_supervisor_obligations_delivery ON supervisor_obligations "
        "(lifecycle,retry_at,created_at,id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_supervisor_obligations_open_source ON supervisor_obligations "
        "(kind,source_identity) WHERE lifecycle IN ('pending','delivered','acknowledged','failed')"
    )
    op.execute(
        "CREATE INDEX idx_supervisor_obligations_item ON supervisor_obligations "
        "(sprint_item_id,lifecycle,created_at,id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_conversation_events_sender_message_id "
        "ON conversation_events(conversation_id,json_extract(payload,'$.sender_message_id')) "
        "WHERE kind IN ('prompt','prompt_delivery_refused','prompt_discarded') "
        "AND json_extract(payload,'$.sender_message_id') LIKE 'supervisor_delivery_%'"
    )
    # Backfill current open work. This does not infer historical completion events.
    op.execute(
        "INSERT OR IGNORE INTO supervisor_obligations "
        "(id,sprint_item_id,ticket_id,kind,source_identity,lifecycle,attempt_count,created_at,updated_at) "
        "SELECT 'obl_backfill_' || id || '_' || ticket_status_revision,sprint_item_id,id,"
        "CASE ticket_status WHEN 'awaiting_agent_review' THEN 'agent_review' "
        "WHEN 'awaiting_user_review' THEN 'user_review' WHEN 'blocked' THEN 'blocked' "
        "WHEN 'errored' THEN 'worker_failure' ELSE 'needs_user' END,"
        "'ticket:' || id || ':status:' || ticket_status_revision || ':' || ticket_status,"
        "'pending',0,updated_at,updated_at FROM tickets WHERE sprint_item_id IS NOT NULL "
        "AND ticket_status IN "
        "('awaiting_agent_review','awaiting_user_review','blocked','errored','needs_user')"
    )
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Supervisor obligation migration failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("dropping supervisor obligations loses delivery state")
