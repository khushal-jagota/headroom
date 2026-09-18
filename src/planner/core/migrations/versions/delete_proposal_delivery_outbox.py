"""Delete the proposal delivery outbox and its failure events.

Revision ID: delete_proposal_delivery_outbox
Revises: proposal_delivery_failures
"""

from __future__ import annotations

from alembic import op

revision = "delete_proposal_delivery_outbox"
down_revision = "proposal_delivery_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM conversation_events WHERE kind='proposal_delivery_failed'")
    op.drop_table("proposal_delivery_failures")
    op.drop_table("ticket_rejection_messages")
    op.drop_table("proposal_holder_wakes")


def downgrade() -> None:
    raise NotImplementedError("deleted proposal delivery state cannot be reconstructed")
