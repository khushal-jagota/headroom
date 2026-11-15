"""Recover an unconfirmed original-Mac credential after a lost reply or restart."""

from alembic import op

revision = "retryable_original_credential"
down_revision = "multi_machine_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing closed handovers stay closed; only future pending issuance stores a secret.
    op.execute("ALTER TABLE machine_executor_credentials ADD COLUMN pending_credential TEXT")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
