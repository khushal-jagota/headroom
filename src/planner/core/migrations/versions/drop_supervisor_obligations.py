"""Drop the supervisor obligation tables. The supervisor is woken, not told facts.

Revision ID: drop_supervisor_obligations
Revises: drop_proposal_review_route
"""

from __future__ import annotations

from alembic import op

revision = "drop_supervisor_obligations"
down_revision = "drop_proposal_review_route"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The indexes on these tables go with them. The one index the obligation migration
    # created on another table stays: idx_conversation_events_sender_message_id belongs
    # to the conversation system, which still looks an event up by sender message id.
    op.execute("DROP TABLE IF EXISTS supervisor_obligation_delivery_members")
    op.execute("DROP TABLE IF EXISTS supervisor_obligations")
    op.execute("DROP TABLE IF EXISTS supervisor_obligation_deliveries")
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Supervisor obligation drop failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("the supervisor obligation tables are not coming back")
