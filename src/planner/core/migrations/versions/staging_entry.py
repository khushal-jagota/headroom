"""The staging migration head admitted by the direct sweep upgrade."""

revision = "conversation_background_tasks"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fresh databases continue to the complete schema in the next revision.
    pass


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only.")
