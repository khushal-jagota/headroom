"""Let a notification type name a pending ask, and let the split start quiet.

Revision ID: an_ask_is_its_own_notification
Revises: skill_rows_teach_what_exists

A permission ask and an unread message shared one notification type. Both were OR-ed
into ``awaiting_reply``, and the attention record only raises an edge on a rising
change, so a message that arrived while an ask was already pending raised nothing and
notified nobody. An ask also announced itself as a message, which it is not.

``awaiting_answer`` is the type that names the ask. Four tables restrict
``notification_type`` with a CHECK, and SQLite cannot alter a CHECK, so each of the
four is rebuilt. The rebuild is done from the table's own stored DDL rather than from
reflection: reflection does not carry CHECK constraints, and every column, foreign
key and CHECK this database actually has is already written down in ``sqlite_master``.
The one explicit index on these tables is captured before the drop and put back after.

The second half is why this revision touches data at all. On the first attention
capture after the split, every conversation that already holds a pending ask would
look like a brand new one: no prior row for ``awaiting_answer``, so a rising edge, so
a push. That is a burst of notifications about asks the owner has already seen. So
this revision writes the resting state the split implies — ``awaiting_answer``
already active wherever an ask is pending — and the first capture finds no rise.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.engine import Connection

revision = "an_ask_is_its_own_notification"
down_revision = "skill_rows_teach_what_exists"
branch_labels = None
depends_on = None

_TABLES_WITH_THE_CHECK = (
    "notification_preferences",
    "notification_attention_state",
    "notification_attention_edges",
    "notification_deliveries",
)

_OLD_TYPES = "('awaiting_reply','awaiting_approval','assigned','errored')"
_NEW_TYPES = "('awaiting_reply','awaiting_answer','awaiting_approval','assigned','errored')"

# The conversations that are waiting on the owner for an answer right now. This is the
# same pair of facts the runtime query derives ``pending_ask`` from: a permission ask or
# a question, with no answer after it.
_CONVERSATIONS_WITH_A_PENDING_ASK = """
SELECT c.conversation_id FROM conversations c WHERE
EXISTS (SELECT 1 FROM conversation_events asked WHERE
asked.conversation_id = c.conversation_id AND asked.kind = 'permission_asked'
AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE
answered.conversation_id = c.conversation_id AND answered.kind = 'permission_answered'
AND json_extract(answered.payload, '$.ask_id') = json_extract(asked.payload, '$.ask_id')
AND answered.sequence > asked.sequence))
OR EXISTS (SELECT 1 FROM conversation_events asked WHERE
asked.conversation_id = c.conversation_id AND asked.kind = 'user_input_requested'
AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE
answered.conversation_id = c.conversation_id
AND answered.kind IN ('user_input_answered','user_input_failed')
AND json_extract(answered.payload, '$.request_id') = json_extract(asked.payload, '$.request_id')
AND answered.sequence > asked.sequence))
"""


def _widen_one_table(conn: Connection, table: str) -> None:
    """Rebuild one table with the wider CHECK, from the DDL it already carries."""
    stored = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if stored is None:
        return
    definition = str(stored[0])
    if _OLD_TYPES not in definition:
        return
    indexes = [
        str(row[0])
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL",
            (table,),
        ).fetchall()
    ]
    rebuilt = definition.replace(_OLD_TYPES, _NEW_TYPES)
    for quoted in (f'"{table}"', f"`{table}`", f"[{table}]", table):
        if quoted in rebuilt:
            rebuilt = rebuilt.replace(quoted, f'"{table}__rebuilt"', 1)
            break
    conn.exec_driver_sql(rebuilt)
    conn.exec_driver_sql(f'INSERT INTO "{table}__rebuilt" SELECT * FROM "{table}"')
    conn.exec_driver_sql(f'DROP TABLE "{table}"')
    conn.exec_driver_sql(f'ALTER TABLE "{table}__rebuilt" RENAME TO "{table}"')
    for index in indexes:
        conn.exec_driver_sql(index)


def upgrade() -> None:
    conn = op.get_bind()
    for table in _TABLES_WITH_THE_CHECK:
        _widen_one_table(conn, table)

    unwidened = [
        str(row[0])
        for row in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('notification_preferences','notification_attention_state',"
            "'notification_attention_edges','notification_deliveries') "
            "AND sql LIKE '%' || ? || '%'",
            (_OLD_TYPES,),
        ).fetchall()
    ]
    if unwidened:
        # Inside the transaction, so a rebuild that did not land rolls back rather than
        # leaving a schema that rejects every row the new code writes.
        raise RuntimeError(
            "awaiting_answer is still refused by: " + ", ".join(sorted(unwidened))
        )

    # Start the split at rest. A subject whose conversation already holds a pending ask
    # gets its resting row now, so the first capture after this deploy sees no rise.
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO notification_attention_state"
        "(subject_kind, subject_id, notification_type, active, generation) "
        "SELECT 'ticket', t.id, 'awaiting_answer', 1, 0 FROM tickets t "
        f"WHERE t.conversation_id IN ({_CONVERSATIONS_WITH_A_PENDING_ASK})"
    )
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO notification_attention_state"
        "(subject_kind, subject_id, notification_type, active, generation) "
        "SELECT CASE WHEN i.id IS NULL THEN 'agent' ELSE 'sprint_item' END, "
        "COALESCE(i.id, a.agent_key), 'awaiting_answer', 1, 0 FROM agents a "
        "LEFT JOIN sprint_items i ON i.supervisor_agent_key = a.agent_key "
        "WHERE (a.agent_key = 'chief_of_staff' OR i.id IS NOT NULL) "
        f"AND a.conversation_id IN ({_CONVERSATIONS_WITH_A_PENDING_ASK})"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
