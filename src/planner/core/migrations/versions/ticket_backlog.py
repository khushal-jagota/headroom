"""Explicit Ticket deferral, independent of Stage and placement."""

from alembic import op

revision = "ticket_backlog"
down_revision = "skills_retire_calendar_guidance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE tickets ADD COLUMN is_backlogged INTEGER NOT NULL DEFAULT 0 "
        "CHECK (is_backlogged IN (0,1))"
    )


def downgrade() -> None:
    raise RuntimeError("Ticket backlog is forward-only")
