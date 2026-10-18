"""Consume automatic notice permission and hold unresolved legacy boundaries."""

from alembic import op

revision = "consume_memory_notice_authorization"
down_revision = "scheduled_ticket_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "CREATE TABLE worker_memory_notice_reservations ("
        "ticket_id TEXT NOT NULL, conversation_id TEXT NOT NULL, "
        "context_compacted_sequence INTEGER NOT NULL CHECK(context_compacted_sequence > 0), "
        "sender_message_id TEXT NOT NULL UNIQUE, consumed_at INTEGER NOT NULL, "
        "PRIMARY KEY(ticket_id,conversation_id,context_compacted_sequence))"
    )
    conn.exec_driver_sql(
        "CREATE TABLE worker_memory_notice_holds ("
        "ticket_id TEXT NOT NULL, conversation_id TEXT NOT NULL, "
        "context_compacted_sequence INTEGER, created_at INTEGER NOT NULL, reason TEXT NOT NULL, "
        "CHECK(context_compacted_sequence IS NULL OR context_compacted_sequence > 0))"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX worker_memory_notice_event_hold "
        "ON worker_memory_notice_holds(ticket_id,conversation_id,context_compacted_sequence) "
        "WHERE context_compacted_sequence IS NOT NULL"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX worker_memory_notice_conversation_hold "
        "ON worker_memory_notice_holds(ticket_id,conversation_id) "
        "WHERE context_compacted_sequence IS NULL"
    )
    # Preserve known event identity. Missing relevant event history holds the entire
    # conversation, including later events. Historical links also retain this policy.
    conn.exec_driver_sql(
        "WITH links AS (SELECT id AS ticket_id,conversation_id FROM tickets "
        "WHERE conversation_id IS NOT NULL UNION "
        "SELECT ticket_id,conversation_id FROM ticket_conversations), "
        "boundaries AS (SELECT l.ticket_id,c.conversation_id,"
        "c.automatically_compacted_through_sequence AS coverage,"
        "(SELECT MAX(e.sequence) FROM conversation_events e "
        "WHERE e.conversation_id=c.conversation_id AND e.kind='context_compacted') AS event,"
        "COALESCE((SELECT MAX(p.sequence) FROM conversation_events p "
        "WHERE p.conversation_id=c.conversation_id AND p.kind='prompt' AND "
        "substr(json_extract(p.payload,'$.sender_message_id'),1,12)='worker_step_'),0) AS answer "
        "FROM links l JOIN conversations c ON c.conversation_id=l.conversation_id) "
        "INSERT INTO worker_memory_notice_holds "
        "SELECT b.ticket_id,b.conversation_id,e.sequence,"
        "CAST(strftime('%s','now') AS INTEGER),'legacy_unresolved_boundary' "
        "FROM boundaries b JOIN conversation_events e ON e.conversation_id=b.conversation_id "
        "AND e.kind='context_compacted' AND e.sequence > b.answer "
        "UNION ALL SELECT ticket_id,conversation_id,NULL,"
        "CAST(strftime('%s','now') AS INTEGER),'legacy_unresolved_boundary' "
        "FROM boundaries WHERE coverage > answer AND (event IS NULL OR event <= answer)"
    )


def downgrade() -> None:
    raise RuntimeError("Automatic memory notice consumption is forward-only")
