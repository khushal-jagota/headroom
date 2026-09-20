"""Make the rising-edge attention model the only way a notification comes about.

A fact, a decision, an intent and a projection cursor were four stages recording a
decision the attention state already held. The state and its edges say what needs the
owner and when that started, so the edge is the notification. What is left is the state,
its edges, and a log of what was actually sent to which device.

The legacy fact path was already dormant: its last fact is 17 August, and every fact
after 18 September is an attention edge. Dropping it removes no live behaviour.

Delivery rows that came from an edge keep their history, because the edge can still say
what they were. Delivery rows from the legacy path go with the facts: their text lives in
``notification_intents``, so a kept row would look like history and answer nothing.

Revision ID: one_notification_path
Revises: derive_ticket_status
"""

from __future__ import annotations

import logging

from alembic import op

revision = "one_notification_path"
down_revision = "derive_ticket_status"
branch_labels = None
depends_on = None

_LOG = logging.getLogger("alembic.runtime.migration")

_EDGE_FACT_PREFIX = "attention:"


def _edge_key(fact_id: str) -> tuple[str, str, str, int] | None:
    """Split ``attention:<kind>:<id>:<type>:<generation>`` back into its edge.

    The split happens here, in Python, because the same split written in SQL is a nest
    of ``instr`` and ``substr`` calls that nobody can read or check.
    """
    if not fact_id.startswith(_EDGE_FACT_PREFIX):
        return None
    parts = fact_id.split(":")
    if len(parts) != 5 or not parts[4].isdigit():
        return None
    _, subject_kind, subject_id, notification_type, generation = parts
    return subject_kind, subject_id, notification_type, int(generation)


def _rebuild_delivery_log() -> None:
    connection = op.get_bind()
    total = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM notification_deliveries"
    ).scalar_one()
    op.execute(
        """
        CREATE TABLE notification_delivery_log (
          subject_kind      TEXT NOT NULL
                            CHECK (subject_kind IN ('ticket','agent','sprint_item')),
          subject_id        TEXT NOT NULL,
          notification_type TEXT NOT NULL CHECK (notification_type IN
                            ('awaiting_reply','awaiting_approval','assigned','errored')),
          generation        INTEGER NOT NULL CHECK (generation > 0),
          subscription_id   TEXT NOT NULL
                            REFERENCES notification_push_subscriptions(subscription_id)
                            ON DELETE CASCADE,
          title             TEXT NOT NULL,
          body              TEXT NOT NULL,
          route             TEXT NOT NULL,
          tag               TEXT NOT NULL,
          created_at        INTEGER NOT NULL,
          status            TEXT NOT NULL
                            CHECK (status IN ('pending','delivered','retry','expired')),
          attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          next_attempt_at   INTEGER NOT NULL,
          last_error        TEXT,
          delivered_at      INTEGER,
          PRIMARY KEY (subject_kind, subject_id, notification_type, generation,
                       subscription_id)
        )
        """
    )
    rows = connection.exec_driver_sql(
        "SELECT d.fact_id, d.subscription_id, d.status, d.attempts, d.next_attempt_at, "
        "d.last_error, d.delivered_at, i.title, i.body, i.route, i.tag, i.created_at "
        "FROM notification_deliveries d "
        "JOIN notification_intents i ON i.fact_id = d.fact_id "
        "ORDER BY d.fact_id, d.subscription_id"
    ).fetchall()
    kept = 0
    for row in rows:
        key = _edge_key(str(row[0]))
        if key is None:
            continue
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO notification_delivery_log"
            "(subject_kind, subject_id, notification_type, generation, subscription_id, "
            "title, body, route, tag, created_at, status, attempts, next_attempt_at, "
            "last_error, delivered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *key,
                str(row[1]),
                str(row[7]),
                str(row[8]),
                str(row[9]),
                str(row[10]),
                int(row[11]),
                str(row[2]),
                int(row[3]),
                int(row[4]),
                row[5],
                row[6],
            ),
        )
        kept += 1
    _LOG.info(
        "one_notification_path: delivery rows before %s, kept from edges %s, dropped %s",
        total,
        kept,
        total - kept,
    )


def upgrade() -> None:
    _rebuild_delivery_log()
    op.execute("DROP TABLE notification_deliveries")
    op.execute("DROP TABLE notification_intents")
    op.execute("DROP TABLE notification_decisions")
    op.execute("DROP TABLE notification_facts")
    op.execute("DROP TABLE notification_projection_cursors")
    op.execute("ALTER TABLE notification_delivery_log RENAME TO notification_deliveries")
    op.execute(
        "CREATE INDEX idx_notification_deliveries_due "
        "ON notification_deliveries(status, next_attempt_at)"
    )
    # The flag now records the whole decision, not one stage of it: a suppressed edge is
    # decided and leaves nothing behind.
    op.execute("ALTER TABLE notification_attention_edges RENAME COLUMN projected TO decided")
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"one-notification-path migration failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("dropped notification history cannot be reconstructed")
