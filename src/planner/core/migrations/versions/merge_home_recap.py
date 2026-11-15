"""Join the Mac home and staging recap migration histories."""

revision = "merge_home_recap"
down_revision = ("recap_freshness", "retryable_original_credential")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
