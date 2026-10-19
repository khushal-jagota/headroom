"""Record approval evidence independently for each Ticket field."""

from alembic import op

revision = "ticket_field_approvals"
down_revision = "navigation_current_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tickets ADD COLUMN field_approvals TEXT NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
