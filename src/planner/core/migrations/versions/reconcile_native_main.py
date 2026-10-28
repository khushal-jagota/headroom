"""Join installed native iOS and current main database histories."""

revision = "reconcile_native_main"
down_revision = ("reconcile_native_recovery", "owner_message_preview")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
