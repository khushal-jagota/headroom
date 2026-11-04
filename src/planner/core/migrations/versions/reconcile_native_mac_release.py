"""Join the installed native app and Mac execution release histories."""

revision = "reconcile_native_mac_release"
down_revision = ("reconcile_native_main", "ticket_integrity_reason")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
