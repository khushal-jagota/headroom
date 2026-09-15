"""Bound proposal wake refusals and retain their visibility intent.

Revision ID: proposal_delivery_failures
Revises: work_attention
"""

from __future__ import annotations

from alembic import op

revision = "proposal_delivery_failures"
down_revision = "work_attention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE proposal_holder_wakes RENAME TO _old_proposal_holder_wakes")
    op.execute(
        "CREATE TABLE proposal_holder_wakes ("
        "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,"
        "proposal_generation INTEGER NOT NULL CHECK (proposal_generation >= 1),"
        "delivery_attempt INTEGER NOT NULL CHECK (delivery_attempt >= 1),"
        "holder_kind TEXT NOT NULL CHECK (holder_kind IN ('chief','sprint_item','ticket')),"
        "holder_id TEXT NOT NULL CHECK (length(trim(holder_id)) > 0),"
        "message TEXT NOT NULL CHECK (length(trim(message)) > 0),"
        "state TEXT NOT NULL CHECK "
        "(state IN ('pending','delivering','delivered','uncertain','cancelled','failed')),"
        "retry_at INTEGER NOT NULL,last_error TEXT,created_at INTEGER NOT NULL,"
        "updated_at INTEGER NOT NULL,delivered_at INTEGER)"
    )
    op.execute(
        "INSERT INTO proposal_holder_wakes "
        "SELECT * FROM _old_proposal_holder_wakes"
    )
    op.execute("DROP TABLE _old_proposal_holder_wakes")
    op.execute(
        "CREATE INDEX idx_proposal_holder_wakes_due ON proposal_holder_wakes"
        "(state,retry_at,ticket_id)"
    )
    op.execute(
        "CREATE TABLE proposal_delivery_failures ("
        "ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,"
        "proposal_generation INTEGER NOT NULL CHECK (proposal_generation >= 1),"
        "conversation_id TEXT REFERENCES conversations(conversation_id),"
        "attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),"
        "last_error TEXT NOT NULL CHECK (length(trim(last_error)) > 0),"
        "visibility_message_id TEXT NOT NULL UNIQUE "
        "CHECK (length(trim(visibility_message_id)) > 0),"
        "created_at INTEGER NOT NULL,resolved_at INTEGER,"
        "PRIMARY KEY(ticket_id,proposal_generation))"
    )
    op.execute(
        "CREATE INDEX idx_proposal_delivery_failures_unresolved "
        "ON proposal_delivery_failures(ticket_id,resolved_at,proposal_generation)"
    )


def downgrade() -> None:
    raise NotImplementedError("proposal delivery failure history cannot be discarded")
