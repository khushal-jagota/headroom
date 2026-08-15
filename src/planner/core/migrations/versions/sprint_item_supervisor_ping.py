"""Give a Sprint Item supervisor a ping, and give the Item its own notification subject.

Two things arrive together, because one is the reason for the other.

The Item gains ``supervisor_ping_sequence`` and ``supervisor_ping_at``: where in its
supervisor's conversation the last ping stands, and when it happened. That position is
what the Workspace row compares against how far the reader has got.

A ping is also a notification, and its subject is the Item, not the supervisor agent:
the push says the Item's title and opens the Item. So the fact table admits the
``sprint_item`` subject kind and the ``sprint_item_ping`` source kind, and the preference
table admits the shared ``sprint_item_supervisors`` subject. The four notification tables
are rebuilt together so that every child keeps pointing at the rebuilt parent, which is
the shape the ``notification_subjects`` revision established.

Existing facts are copied byte for byte. Supervisor conversation facts already recorded
under the agent subject stay there: history is durable, and every one of them was
suppressed.

The failure switch for the new subject is seeded off. Supervisor turns fail about thirty
times a day, and an upgrade must not start pushing something nobody asked for. The ping
itself takes its type default, which is on.

Revision ID: sprint_item_supervisor_ping
Revises: one_approval_gate
"""

from __future__ import annotations

from alembic import op

revision = "sprint_item_supervisor_ping"
down_revision = "one_approval_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sprint_items ADD COLUMN supervisor_ping_sequence INTEGER")
    op.execute("ALTER TABLE sprint_items ADD COLUMN supervisor_ping_at INTEGER")

    op.execute(
        """
        CREATE TABLE notification_facts_with_item (
          fact_id            TEXT PRIMARY KEY,
          notification_type  TEXT NOT NULL,
          subject_kind       TEXT NOT NULL
                             CHECK (subject_kind IN ('ticket','agent','sprint_item')),
          ticket_id          TEXT REFERENCES tickets(id) ON DELETE CASCADE,
          agent_key          TEXT REFERENCES agents(agent_key) ON DELETE CASCADE,
          sprint_item_id     TEXT REFERENCES sprint_items(id) ON DELETE CASCADE,
          source_kind        TEXT NOT NULL
                             CHECK (source_kind IN ('ticket','conversation','sprint_item_ping')),
          source_id          TEXT NOT NULL,
          source_sequence    INTEGER NOT NULL CHECK (source_sequence >= 0),
          occurred_at        INTEGER NOT NULL,
          payload            TEXT NOT NULL,
          CHECK (
            (subject_kind = 'ticket' AND ticket_id IS NOT NULL
             AND agent_key IS NULL AND sprint_item_id IS NULL)
            OR
            (subject_kind = 'agent' AND agent_key IS NOT NULL
             AND ticket_id IS NULL AND sprint_item_id IS NULL)
            OR
            (subject_kind = 'sprint_item' AND sprint_item_id IS NOT NULL
             AND ticket_id IS NULL AND agent_key IS NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_decisions_with_item (
          fact_id     TEXT PRIMARY KEY
                      REFERENCES notification_facts_with_item(fact_id)
                      ON DELETE CASCADE,
          outcome     TEXT NOT NULL CHECK (outcome IN ('notify','suppress')),
          decided_at  INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_intents_with_item (
          fact_id     TEXT PRIMARY KEY
                      REFERENCES notification_facts_with_item(fact_id)
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
        CREATE TABLE notification_deliveries_with_item (
          fact_id          TEXT NOT NULL
                           REFERENCES notification_intents_with_item(fact_id)
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
        INSERT INTO notification_facts_with_item(
          fact_id, notification_type, subject_kind, ticket_id, agent_key, sprint_item_id,
          source_kind, source_id, source_sequence, occurred_at, payload
        )
        SELECT fact_id, notification_type, subject_kind, ticket_id, agent_key, NULL,
               source_kind, source_id, source_sequence, occurred_at, payload
        FROM notification_facts
        """
    )
    op.execute(
        """
        INSERT INTO notification_decisions_with_item(fact_id, outcome, decided_at)
        SELECT fact_id, outcome, decided_at FROM notification_decisions
        """
    )
    op.execute(
        """
        INSERT INTO notification_intents_with_item(
          fact_id, title, body, route, tag, created_at
        )
        SELECT fact_id, title, body, route, tag, created_at FROM notification_intents
        """
    )
    op.execute(
        """
        INSERT INTO notification_deliveries_with_item(
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
    op.execute("ALTER TABLE notification_facts_with_item RENAME TO notification_facts")
    op.execute("ALTER TABLE notification_decisions_with_item RENAME TO notification_decisions")
    op.execute("ALTER TABLE notification_intents_with_item RENAME TO notification_intents")
    op.execute("ALTER TABLE notification_deliveries_with_item RENAME TO notification_deliveries")
    op.execute(
        "CREATE INDEX idx_notification_facts_undecided ON notification_facts(occurred_at, fact_id)"
    )
    op.execute(
        "CREATE INDEX idx_notification_deliveries_due "
        "ON notification_deliveries(status, next_attempt_at)"
    )

    op.execute(
        """
        CREATE TABLE notification_projection_cursors_with_item (
          source_kind TEXT NOT NULL
                      CHECK (source_kind IN ('ticket','conversation','sprint_item_ping')),
          source_id   TEXT NOT NULL,
          sequence    INTEGER NOT NULL CHECK (sequence >= 0),
          PRIMARY KEY (source_kind, source_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO notification_projection_cursors_with_item(source_kind, source_id, sequence)
        SELECT source_kind, source_id, sequence FROM notification_projection_cursors
        """
    )
    op.execute("DROP TABLE notification_projection_cursors")
    op.execute(
        "ALTER TABLE notification_projection_cursors_with_item "
        "RENAME TO notification_projection_cursors"
    )

    op.execute(
        """
        CREATE TABLE notification_preferences_with_item (
          subject_key       TEXT NOT NULL
                            CHECK (subject_key IN (
                              'tickets','chief_of_staff','sprint_item_supervisors'
                            )),
          notification_type TEXT NOT NULL,
          enabled           INTEGER NOT NULL CHECK (enabled IN (0,1)),
          updated_at        INTEGER NOT NULL,
          PRIMARY KEY (subject_key, notification_type),
          CHECK (
            (subject_key = 'tickets' AND notification_type IN (
              'ticket_needs_approval', 'needs_input', 'permission_requested',
              'worker_completed', 'worker_failed'
            ))
            OR
            (subject_key = 'chief_of_staff' AND notification_type IN (
              'needs_input', 'permission_requested', 'worker_completed', 'worker_failed'
            ))
            OR
            (subject_key = 'sprint_item_supervisors' AND notification_type IN (
              'sprint_item_ping', 'worker_failed'
            ))
          )
        )
        """
    )
    op.execute(
        """
        INSERT INTO notification_preferences_with_item(
          subject_key, notification_type, enabled, updated_at
        )
        SELECT subject_key, notification_type, enabled, updated_at
        FROM notification_preferences
        """
    )
    op.execute("DROP TABLE notification_preferences")
    op.execute(
        "ALTER TABLE notification_preferences_with_item RENAME TO notification_preferences"
    )
    op.execute(
        """
        INSERT OR IGNORE INTO notification_preferences(
          subject_key, notification_type, enabled, updated_at
        )
        VALUES ('sprint_item_supervisors', 'worker_failed', 0, 0)
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Sprint Item notification facts cannot be represented by the earlier subject schema"
    )
