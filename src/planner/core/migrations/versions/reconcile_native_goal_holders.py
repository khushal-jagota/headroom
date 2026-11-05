"""Join shipped native, Mac contact, and goal-holder histories forward-only."""

revision = "reconcile_native_goal_holders"
down_revision = ("reconcile_native_staging", "mac_contact_status", "goal_holders")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
