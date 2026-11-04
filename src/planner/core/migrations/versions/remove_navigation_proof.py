"""Remove the unused screen-update proof records."""
from alembic import op

revision = "remove_navigation_proof"
down_revision = "remove_scheduled_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE navigation_commit_witnesses")
    op.execute("DROP TABLE navigation_proof_identity")


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
