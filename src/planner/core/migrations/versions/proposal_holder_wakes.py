"""Persist each non-owner proposal holder wake until its conversation accepts it.

Revision ID: proposal_holder_wakes
Revises: proposal_holder
"""

from __future__ import annotations

from alembic import op

revision = "proposal_holder_wakes"
down_revision = "proposal_holder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE proposal_holder_wakes ("
        "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,"
        "proposal_generation INTEGER NOT NULL CHECK (proposal_generation >= 1),"
        "delivery_attempt INTEGER NOT NULL CHECK (delivery_attempt >= 1),"
        "holder_kind TEXT NOT NULL CHECK (holder_kind IN ('chief','sprint_item','ticket')),"
        "holder_id TEXT NOT NULL CHECK (length(trim(holder_id)) > 0),"
        "message TEXT NOT NULL CHECK (length(trim(message)) > 0),"
        "state TEXT NOT NULL CHECK (state IN ('pending','delivered','cancelled')),"
        "retry_at INTEGER NOT NULL,"
        "last_error TEXT,"
        "created_at INTEGER NOT NULL,"
        "updated_at INTEGER NOT NULL,"
        "delivered_at INTEGER)"
    )
    op.execute(
        "CREATE INDEX idx_proposal_holder_wakes_due ON proposal_holder_wakes"
        "(state,retry_at,ticket_id)"
    )


def downgrade() -> None:
    raise NotImplementedError("proposal holder wake delivery state cannot be discarded")
