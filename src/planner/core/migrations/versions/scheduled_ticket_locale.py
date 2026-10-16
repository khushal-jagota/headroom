"""Persist the resolved execution locale of schedule templates."""

from alembic import op

revision = "scheduled_ticket_locale"
down_revision = "ticket_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE scheduled_ticket_schedules ADD COLUMN locale TEXT NOT NULL "
        "DEFAULT 'vps' CHECK (locale IN ('vps', 'macos'))"
    )


def downgrade() -> None:
    raise RuntimeError("Schedule locale is forward-only")
