"""Retain the operator-selected artifact platform for each executor credential."""
from alembic import op

revision = "linux_executor_platform"
down_revision = "retryable_original_credential"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE machine_executor_credentials ADD COLUMN platform TEXT NOT NULL "
               "DEFAULT 'darwin' CHECK(platform IN ('darwin','linux'))")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
