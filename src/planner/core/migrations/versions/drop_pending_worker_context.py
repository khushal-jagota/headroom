"""Drop generic context that no worker-step path reads.

Revision ID: drop_pending_worker_context
Revises: remove_ticket_alias_and_backend_error
"""

from __future__ import annotations

from alembic import op

revision = "drop_pending_worker_context"
down_revision = "remove_ticket_alias_and_backend_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("pending_worker_context")


def downgrade() -> None:
    raise NotImplementedError("discarded pending worker context cannot be reconstructed")
