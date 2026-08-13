"""Add the Ticket-owned judgment record and its first optional verdict.

Revision ID: ticket_judgments
Revises: weekly_sprint_checkpoint_schedule
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text

revision = "ticket_judgments"
down_revision = "weekly_sprint_checkpoint_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_judgments",
        Column(
            "ticket_id",
            Text,
            ForeignKey("tickets.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        Column("verdict_rating", Integer),
        Column("verdict_text", Text),
        CheckConstraint(
            "verdict_rating IS NULL OR verdict_rating BETWEEN 1 AND 5",
            name="ck_ticket_judgments_verdict_rating",
        ),
    )


def downgrade() -> None:
    op.drop_table("ticket_judgments")

