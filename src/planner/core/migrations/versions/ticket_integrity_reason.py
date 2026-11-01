"""Keep the startup audit's reason independent of human text and Worker claims."""

from alembic import op

revision = "ticket_integrity_reason"
down_revision = "executor_default_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tickets ADD COLUMN integrity_error_reason TEXT")
    # Defer projection refresh until the startup audit records any invalid Tickets.
    op.execute("INSERT OR IGNORE INTO navigation_dirty(kind,id) SELECT 'tickets',id FROM tickets")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
