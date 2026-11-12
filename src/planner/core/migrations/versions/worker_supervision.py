"""Associate durable local custody with the portable Worker process record."""
from alembic import op

revision = "worker_supervision"
down_revision = "named_machines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_child_custody ADD COLUMN supervision_record TEXT")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
