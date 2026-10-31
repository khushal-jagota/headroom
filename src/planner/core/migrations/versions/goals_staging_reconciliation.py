"""Join accepted goal data and current staging's preview/upload history."""

revision = "goals_staging_reconciliation"
down_revision = ("ticket_application_previews", "scalar_observation_coverage")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Reconciliation is forward-only. Restore the matching backup.")
