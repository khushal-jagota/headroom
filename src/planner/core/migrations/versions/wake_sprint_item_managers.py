"""Add durable Sprint Item manager wakes and backfill current attention.

Revision ID: wake_sprint_item_managers
Revises: an_ask_is_its_own_notification
"""

from __future__ import annotations

from alembic import op

revision = "wake_sprint_item_managers"
down_revision = "an_ask_is_its_own_notification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE tickets ADD COLUMN pending_proposal_revision INTEGER NOT NULL "
        "DEFAULT 0 CHECK (pending_proposal_revision >= 0)"
    )
    conn.exec_driver_sql(
        "UPDATE tickets SET pending_proposal_revision=1 WHERE pending_proposal IS NOT NULL"
    )
    conn.exec_driver_sql(
        "CREATE TABLE manager_wakes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "sprint_item_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE,"
        "ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,"
        "source_kind TEXT NOT NULL CHECK (source_kind IN ('proposal','worker_error')),"
        "source_revision INTEGER NOT NULL CHECK (source_revision > 0),"
        "summary TEXT NOT NULL,created_at INTEGER NOT NULL,closed_at INTEGER,"
        "UNIQUE(ticket_id,source_kind,source_revision))"
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_manager_wakes_open ON manager_wakes(sprint_item_id,created_at,id) "
        "WHERE closed_at IS NULL"
    )
    conn.exec_driver_sql(
        "CREATE TABLE manager_wake_batches ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "sprint_item_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE,"
        "sender_message_id TEXT NOT NULL UNIQUE,"
        "message TEXT NOT NULL,"
        "status TEXT NOT NULL CHECK (status IN "
        "('pending','dispatching','accepted','uncertain','refused','discarded','delivered')),"
        "conversation_id TEXT,process_token TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_manager_wake_batches_outcome ON manager_wake_batches(status,id)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE manager_wake_batch_members ("
        "batch_id INTEGER NOT NULL REFERENCES manager_wake_batches(id) ON DELETE CASCADE,"
        "wake_id INTEGER NOT NULL REFERENCES manager_wakes(id) ON DELETE CASCADE,"
        "PRIMARY KEY(batch_id,wake_id))"
    )

    # A migration only knows current unresolved state. Historical settled proposals and
    # cleared errors no longer need attention and have no source row to reconstruct.
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO manager_wakes("
        "sprint_item_id,ticket_id,source_kind,source_revision,summary,created_at) "
        "SELECT json_extract(t.ceiling_holder,'$.id'),t.id,'proposal',"
        "t.pending_proposal_revision,"
        "'Ticket `' || t.id || '` (' || t.title || ') filed a proposal for `' || "
        "json_extract(t.pending_proposal,'$.field') || '`.',t.updated_at "
        "FROM tickets t JOIN sprint_items i "
        "ON i.id=json_extract(t.ceiling_holder,'$.id') AND i.kind='normal' "
        "WHERE t.pending_proposal IS NOT NULL "
        "AND json_extract(t.ceiling_holder,'$.kind')='sprint_item'"
    )
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO manager_wakes("
        "sprint_item_id,ticket_id,source_kind,source_revision,summary,created_at) "
        "SELECT t.sprint_item_id,t.id,'worker_error',t.worker_step_claim_revision,"
        "'Ticket `' || t.id || '` (' || t.title || "
        "') entered the explicit worker-error state.',t.worker_step_claim_changed_at "
        "FROM tickets t JOIN sprint_items i ON i.id=t.sprint_item_id AND i.kind='normal' "
        "WHERE t.worker_step_claim='errored' AND t.worker_step_claim_revision > 0"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
