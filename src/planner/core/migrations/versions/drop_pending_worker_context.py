"""Drop generic context that no worker-step path reads.

Revision ID: drop_pending_worker_context
Revises: ticket_blocks
"""

from __future__ import annotations

from alembic import op

revision = "drop_pending_worker_context"
down_revision = "ticket_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("pending_worker_context")


def downgrade() -> None:
    raise NotImplementedError("discarded pending worker context cannot be reconstructed")
