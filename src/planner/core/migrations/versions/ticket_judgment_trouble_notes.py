"""Add append-only worker trouble notes to Ticket judgments.

Revision ID: ticket_judgment_trouble_notes
Revises: ticket_judgments
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, ForeignKey, Integer, PrimaryKeyConstraint, Text

revision = "ticket_judgment_trouble_notes"
down_revision = "ticket_conversation_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_judgment_trouble_notes",
        Column(
            "ticket_id",
            Text,
            ForeignKey("ticket_judgments.ticket_id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("sequence", Integer, nullable=False),
        Column("body", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        PrimaryKeyConstraint(
            "ticket_id",
            "sequence",
            name="pk_ticket_judgment_trouble_notes",
        ),
    )


def downgrade() -> None:
    op.drop_table("ticket_judgment_trouble_notes")
