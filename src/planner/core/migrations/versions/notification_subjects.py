"""Give notification facts a typed Ticket-or-agent subject.

The first notification record could only name a Ticket. Facts, decisions, intents,
deliveries, and projection cursors are durable history, so this revision changes only
the fact table's subject columns. Every existing fact is copied byte-for-byte apart
from translating its Ticket foreign key into ``('ticket', ticket_id)``. Existing
payloads remain untouched, including facts that have not reached policy yet.

Current agent conversation positions are also seeded. That makes this revision the
no-history boundary for agents just as the original notification revision was for
Tickets: an already-finished Chief turn cannot become a new push after upgrade.

Revision ID: notification_subjects
Revises: notifications
"""

from __future__ import annotations

from alembic import op

revision = "notification_subjects"
down_revision = "notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notification_facts_with_subject (
          fact_id            TEXT PRIMARY KEY,
          notification_type  TEXT NOT NULL,
          subject_kind       TEXT NOT NULL CHECK (subject_kind IN ('ticket','agent')),
          ticket_id          TEXT REFERENCES tickets(id) ON DELETE CASCADE,
          agent_key          TEXT REFERENCES agents(agent_key) ON DELETE CASCADE,
          source_kind        TEXT NOT NULL CHECK (source_kind IN ('ticket','conversation')),
          source_id          TEXT NOT NULL,
          source_sequence    INTEGER NOT NULL CHECK (source_sequence >= 0),
          occurred_at        INTEGER NOT NULL,
          payload            TEXT NOT NULL,
          CHECK (
            (subject_kind = 'ticket' AND ticket_id IS NOT NULL AND agent_key IS NULL)
            OR
            (subject_kind = 'agent' AND agent_key IS NOT NULL AND ticket_id IS NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_decisions_with_subject (
          fact_id     TEXT PRIMARY KEY
                      REFERENCES notification_facts_with_subject(fact_id)
                      ON DELETE CASCADE,
          outcome     TEXT NOT NULL CHECK (outcome IN ('notify','suppress')),
          decided_at  INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_intents_with_subject (
          fact_id     TEXT PRIMARY KEY
                      REFERENCES notification_facts_with_subject(fact_id)
                      ON DELETE CASCADE,
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
        CREATE TABLE notification_deliveries_with_subject (
          fact_id          TEXT NOT NULL
                           REFERENCES notification_intents_with_subject(fact_id)
                           ON DELETE CASCADE,
          subscription_id  TEXT NOT NULL
                           REFERENCES notification_push_subscriptions(subscription_id)
                           ON DELETE CASCADE,
          status           TEXT NOT NULL
                           CHECK (status IN ('pending','delivered','retry','expired')),
          attempts         INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          next_attempt_at  INTEGER NOT NULL,
          last_error       TEXT,
          delivered_at     INTEGER,
          PRIMARY KEY (fact_id, subscription_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO notification_facts_with_subject(
          fact_id, notification_type, subject_kind, ticket_id, agent_key, source_kind,
          source_id, source_sequence, occurred_at, payload
        )
        SELECT fact_id, notification_type, 'ticket', ticket_id, NULL, source_kind,
               source_id, source_sequence, occurred_at, payload
        FROM notification_facts
        """
    )
    op.execute(
        """
        INSERT INTO notification_decisions_with_subject(
          fact_id, outcome, decided_at
        )
        SELECT fact_id, outcome, decided_at FROM notification_decisions
        """
    )
    op.execute(
        """
        INSERT INTO notification_intents_with_subject(
          fact_id, title, body, route, tag, created_at
        )
        SELECT fact_id, title, body, route, tag, created_at
        FROM notification_intents
        """
    )
    op.execute(
        """
        INSERT INTO notification_deliveries_with_subject(
          fact_id, subscription_id, status, attempts, next_attempt_at,
          last_error, delivered_at
        )
        SELECT fact_id, subscription_id, status, attempts, next_attempt_at,
               last_error, delivered_at
        FROM notification_deliveries
        """
    )
    op.execute("DROP TABLE notification_deliveries")
    op.execute("DROP TABLE notification_intents")
    op.execute("DROP TABLE notification_decisions")
    op.execute("DROP TABLE notification_facts")
    op.execute("ALTER TABLE notification_facts_with_subject RENAME TO notification_facts")
    op.execute("ALTER TABLE notification_decisions_with_subject RENAME TO notification_decisions")
    op.execute("ALTER TABLE notification_intents_with_subject RENAME TO notification_intents")
    op.execute("ALTER TABLE notification_deliveries_with_subject RENAME TO notification_deliveries")
    op.execute(
        "CREATE INDEX idx_notification_facts_undecided ON notification_facts(occurred_at, fact_id)"
    )
    op.execute(
        "CREATE INDEX idx_notification_deliveries_due "
        "ON notification_deliveries(status, next_attempt_at)"
    )
    op.execute(
        """
        INSERT OR IGNORE INTO notification_projection_cursors(
          source_kind, source_id, sequence
        )
        SELECT 'conversation', c.conversation_id, c.latest_sequence
        FROM agents a
        JOIN conversations c ON c.conversation_id = a.conversation_id
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "agent notification facts cannot be represented by the Ticket-only schema"
    )
