"""Tickets remember when their status last changed; the event log goes away.

The event log existed so the browser could be told what changed and so Review could
work out how long a Ticket had been waiting for its user. The browser is now told only
that something changed, and Review's one question is answered by the Ticket itself, so
the log has no reader left.

The new column is filled from the log before it is dropped: a Ticket's latest recorded
status transition, or — for a Ticket that never recorded one — the last time the Ticket
row was touched, which is the closest honest answer the old data holds.

Revision ID: ticket_status_changed_at
Revises: ticket_status_reshape
"""

from __future__ import annotations

from alembic import op

revision = "ticket_status_changed_at"
down_revision = "ticket_status_reshape"
branch_labels = None
depends_on = None

ADD_COLUMN = """
ALTER TABLE tickets ADD COLUMN ticket_status_changed_at INTEGER NOT NULL DEFAULT 0
"""

# Latest is the highest event id, not the highest timestamp: two transitions inside the
# same second carry the same created_at, and the one written second is the one that says
# where the Ticket ended up.
BACKFILL = """
UPDATE tickets SET ticket_status_changed_at = COALESCE(
  (
    SELECT events.created_at FROM events
     WHERE events.entity_id = tickets.id AND events.kind = 'ticket_status_changed'
     ORDER BY events.id DESC LIMIT 1
  ),
  updated_at
)
"""


def upgrade() -> None:
    op.execute(ADD_COLUMN)
    op.execute(BACKFILL)
    op.execute("DROP INDEX idx_events_entity")
    op.execute("DROP TABLE events")


def downgrade() -> None:
    raise NotImplementedError(
        "the event log's contents cannot be reconstructed once it is dropped"
    )
