"""Join native push delivery and automatic conversation recovery histories."""

revision = "reconcile_native_recovery"
down_revision = ("review_messages", "native_push_deliveries")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
