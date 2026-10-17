"""Join reviewed resident/caller and native usage migration histories."""

revision = "merge_resident_native_usage"
down_revision = ("merge_executor_memory_notice", "native_usage_prices")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both original branches retain their schema and data transformations.
    pass


def downgrade() -> None:
    raise RuntimeError("Resident and native usage integration is forward-only")
