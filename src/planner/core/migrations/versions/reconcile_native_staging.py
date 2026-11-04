"""Join installed native and staging histories without changing either history."""

revision = "reconcile_native_staging"
down_revision = ("reconcile_native_mac_release", "conversation_background_tasks")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
