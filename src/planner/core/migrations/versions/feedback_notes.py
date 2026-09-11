"""Add the durable feedback inbox.

Revision ID: feedback_notes
Revises: durable_outcomes
"""

from __future__ import annotations

from alembic import op

revision = "feedback_notes"
down_revision = "durable_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE feedback_notes (
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          page_address TEXT,
          page_label TEXT,
          state TEXT NOT NULL CHECK (state IN ('open', 'handled')),
          ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          handled_at INTEGER,
          CHECK (
            (page_address IS NULL AND page_label IS NULL) OR
            (page_address IS NOT NULL AND substr(page_address, 1, 2) = '#/' AND
             length(page_address) > 2 AND
             page_label IS NOT NULL AND length(trim(page_label)) > 0)
          ),
          CHECK (
            (state = 'open' AND ticket_id IS NULL AND handled_at IS NULL) OR
            (state = 'handled' AND handled_at IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_feedback_notes_state_created "
        "ON feedback_notes(state, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_feedback_notes_ticket_id ON feedback_notes(ticket_id)")


def downgrade() -> None:
    op.execute("DROP INDEX idx_feedback_notes_ticket_id")
    op.execute("DROP INDEX idx_feedback_notes_state_created")
    op.execute("DROP TABLE feedback_notes")
