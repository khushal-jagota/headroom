"""Install the durable notification policy and Web Push record.

Existing Ticket and conversation positions are seeded as projection cursors. This is
the exact no-history boundary: only facts written after this migration can become a
notification.

Revision ID: notifications
Revises: project_priority
"""

from __future__ import annotations

from alembic import op

revision = "notifications"
down_revision = "project_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tickets ADD COLUMN ticket_status_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK (ticket_status_revision >= 0)"
    )
    op.execute(
        """
        CREATE TABLE notification_preferences (
          notification_type TEXT PRIMARY KEY,
          enabled           INTEGER NOT NULL CHECK (enabled IN (0,1)),
          updated_at        INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_projection_cursors (
          source_kind TEXT NOT NULL CHECK (source_kind IN ('ticket','conversation')),
          source_id   TEXT NOT NULL,
          sequence    INTEGER NOT NULL CHECK (sequence >= 0),
          PRIMARY KEY (source_kind, source_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO notification_projection_cursors(source_kind, source_id, sequence)
        SELECT 'ticket', id, ticket_status_revision FROM tickets
        """
    )
    op.execute(
        """
        INSERT INTO notification_projection_cursors(source_kind, source_id, sequence)
        SELECT 'conversation', conversation_id, latest_sequence FROM conversations
        """
    )
    op.execute(
        """
        CREATE TABLE notification_facts (
          fact_id            TEXT PRIMARY KEY,
          notification_type  TEXT NOT NULL,
          ticket_id          TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
          source_kind        TEXT NOT NULL CHECK (source_kind IN ('ticket','conversation')),
          source_id          TEXT NOT NULL,
          source_sequence    INTEGER NOT NULL CHECK (source_sequence >= 0),
          occurred_at        INTEGER NOT NULL,
          payload            TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_decisions (
          fact_id     TEXT PRIMARY KEY REFERENCES notification_facts(fact_id) ON DELETE CASCADE,
          outcome     TEXT NOT NULL CHECK (outcome IN ('notify','suppress')),
          decided_at  INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_intents (
          fact_id     TEXT PRIMARY KEY REFERENCES notification_facts(fact_id) ON DELETE CASCADE,
          title       TEXT NOT NULL,
          body        TEXT NOT NULL,
          route       TEXT NOT NULL,
          tag         TEXT NOT NULL,
          created_at  INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_push_subscriptions (
          subscription_id TEXT PRIMARY KEY,
          endpoint        TEXT NOT NULL UNIQUE,
          p256dh          TEXT NOT NULL,
          auth            TEXT NOT NULL,
          created_at      INTEGER NOT NULL,
          updated_at      INTEGER NOT NULL,
          disabled_at     INTEGER
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_deliveries (
          fact_id          TEXT NOT NULL REFERENCES notification_intents(fact_id) ON DELETE CASCADE,
          subscription_id  TEXT NOT NULL
                           REFERENCES notification_push_subscriptions(subscription_id)
                           ON DELETE CASCADE,
          status           TEXT NOT NULL
                           CHECK (status IN ('pending','delivered','retry','expired')),
          attempts         INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          next_attempt_at  INTEGER NOT NULL,
          last_error       TEXT,
          delivered_at    INTEGER,
          PRIMARY KEY (fact_id, subscription_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_web_push_identity (
          singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
          private_key TEXT NOT NULL,
          public_key  TEXT NOT NULL,
          created_at  INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_notification_facts_undecided "
        "ON notification_facts(occurred_at, fact_id)"
    )
    op.execute(
        "CREATE INDEX idx_notification_deliveries_due "
        "ON notification_deliveries(status, next_attempt_at)"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "dropping notification tables would discard preferences, subscriptions, and "
        "delivery history"
    )
