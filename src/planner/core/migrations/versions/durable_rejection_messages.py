"""Persist rejection lifecycle and decider messages for outbox delivery.

Revision ID: durable_rejection_messages
Revises: proposal_holder_wakes
"""

from __future__ import annotations

from alembic import op

revision = "durable_rejection_messages"
down_revision = "proposal_holder_wakes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE ticket_rejection_messages ("
        "id TEXT PRIMARY KEY,"
        "ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,"
        "rejection_generation INTEGER NOT NULL CHECK (rejection_generation >= 1),"
        "sequence INTEGER NOT NULL CHECK (sequence IN (1,2)),"
        "delivery_attempt INTEGER NOT NULL CHECK (delivery_attempt >= 1),"
        "message TEXT NOT NULL CHECK (length(trim(message)) > 0),"
        "sender_kind TEXT CHECK "
        "(sender_kind IS NULL OR sender_kind IN ('owner','chief','sprint_item','ticket')),"
        "sender_id TEXT CHECK (sender_id IS NULL OR length(trim(sender_id)) > 0),"
        "state TEXT NOT NULL CHECK "
        "(state IN ('pending','delivering','delivered','uncertain','cancelled')),"
        "retry_at INTEGER NOT NULL,"
        "last_error TEXT,"
        "created_at INTEGER NOT NULL,"
        "updated_at INTEGER NOT NULL,"
        "delivered_at INTEGER,"
        "UNIQUE(ticket_id,rejection_generation,sequence),"
        "CHECK ((sequence=1 AND sender_kind IS NULL AND sender_id IS NULL) OR "
        "(sequence=2 AND sender_kind IS NOT NULL AND sender_id IS NOT NULL)),"
        "CHECK (sender_kind != 'owner' OR sender_id = 'owner'),"
        "CHECK (sender_kind != 'chief' OR sender_id = 'chief'),"
        "CHECK (sender_id IS NULL OR sender_id = trim(sender_id)))"
    )
    op.execute(
        "CREATE INDEX idx_ticket_rejection_messages_due ON ticket_rejection_messages"
        "(state,retry_at,ticket_id,rejection_generation,sequence)"
    )


def downgrade() -> None:
    raise NotImplementedError("durable rejection messages cannot be discarded")
