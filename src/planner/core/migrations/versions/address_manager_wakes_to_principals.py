"""Address durable manager wakes to any employee principal.

Revision ID: address_manager_wakes_to_principals
Revises: remove_redundant_ticket_item_storage
"""

from __future__ import annotations

from alembic import op

revision = "address_manager_wakes_to_principals"
down_revision = "remove_redundant_ticket_item_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "CREATE TEMP TABLE migration_manager_wake_members AS "
        "SELECT batch_id,wake_id FROM manager_wake_batch_members"
    )
    conn.exec_driver_sql("DROP TABLE manager_wake_batch_members")
    conn.exec_driver_sql(
        "CREATE TABLE manager_wakes_new ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "sprint_item_id TEXT,"
        "ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,"
        "source_kind TEXT NOT NULL CHECK "
        "(source_kind IN ('proposal','worker_error','turn_failure')),"
        "source_revision INTEGER NOT NULL CHECK (source_revision > 0),"
        "summary TEXT NOT NULL,created_at INTEGER NOT NULL,closed_at INTEGER,"
        "target_kind TEXT NOT NULL CHECK (target_kind IN ('chief','sprint_item','ticket')),"
        "target_id TEXT NOT NULL,source_conversation_id TEXT)"
    )
    conn.exec_driver_sql(
        "INSERT INTO manager_wakes_new(id,sprint_item_id,ticket_id,source_kind,source_revision,"
        "summary,created_at,closed_at,target_kind,target_id) "
        "SELECT id,sprint_item_id,ticket_id,source_kind,source_revision,summary,created_at,"
        "closed_at,'sprint_item',sprint_item_id FROM manager_wakes"
    )
    conn.exec_driver_sql("DROP INDEX idx_manager_wakes_open")
    conn.exec_driver_sql("ALTER TABLE manager_wakes RENAME TO manager_wakes_old")
    conn.exec_driver_sql("ALTER TABLE manager_wakes_new RENAME TO manager_wakes")
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX idx_manager_wakes_ticket_source ON "
        "manager_wakes(ticket_id,source_kind,source_revision) "
        "WHERE source_kind IN ('proposal','worker_error')"
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_manager_wakes_open_target "
        "ON manager_wakes(target_kind,target_id,created_at,id) WHERE closed_at IS NULL"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX idx_manager_wakes_turn_failure_source "
        "ON manager_wakes(source_conversation_id,source_revision) "
        "WHERE source_kind='turn_failure'"
    )
    conn.exec_driver_sql(
        "CREATE TABLE manager_wake_batches_new ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,sprint_item_id TEXT,"
        "sender_message_id TEXT NOT NULL UNIQUE,message TEXT NOT NULL,"
        "status TEXT NOT NULL CHECK (status IN "
        "('pending','offering','dispatching','accepted','uncertain','refused','discarded','delivered')),"
        "conversation_id TEXT,process_token TEXT,created_at INTEGER NOT NULL,"
        "updated_at INTEGER NOT NULL,target_kind TEXT NOT NULL CHECK "
        "(target_kind IN ('chief','sprint_item','ticket')),target_id TEXT NOT NULL)"
    )
    conn.exec_driver_sql(
        "INSERT INTO manager_wake_batches_new(id,sprint_item_id,sender_message_id,message,"
        "status,conversation_id,process_token,created_at,updated_at,target_kind,target_id) "
        "SELECT id,sprint_item_id,sender_message_id,message,status,conversation_id,"
        "process_token,created_at,updated_at,'sprint_item',sprint_item_id "
        "FROM manager_wake_batches"
    )
    conn.exec_driver_sql("DROP INDEX idx_manager_wake_batches_outcome")
    conn.exec_driver_sql("DROP TABLE manager_wake_batches")
    conn.exec_driver_sql(
        "ALTER TABLE manager_wake_batches_new RENAME TO manager_wake_batches"
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_manager_wake_batches_outcome "
        "ON manager_wake_batches(status,id)"
    )
    conn.exec_driver_sql("DROP TABLE manager_wakes_old")
    conn.exec_driver_sql(
        "CREATE TABLE manager_wake_batch_members ("
        "batch_id INTEGER NOT NULL REFERENCES manager_wake_batches(id) ON DELETE CASCADE,"
        "wake_id INTEGER NOT NULL REFERENCES manager_wakes(id) ON DELETE CASCADE,"
        "PRIMARY KEY(batch_id,wake_id))"
    )
    conn.exec_driver_sql(
        "INSERT INTO manager_wake_batch_members(batch_id,wake_id) "
        "SELECT batch_id,wake_id FROM migration_manager_wake_members"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
