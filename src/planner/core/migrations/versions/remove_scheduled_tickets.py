"""Remove Ticket timer definitions and receipts, retaining all Tickets."""

from alembic import op

revision = "remove_scheduled_tickets"
down_revision = "goal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE scheduled_ticket_occurrences")
    op.execute("DROP TABLE scheduled_ticket_schedules")


def downgrade() -> None:
    raise RuntimeError("Removed schedules and occurrence receipts cannot be restored")
