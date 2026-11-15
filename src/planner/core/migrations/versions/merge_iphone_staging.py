"""Join the iPhone notification and current staging migration histories."""

revision = "merge_iphone_staging"
down_revision = ("notification_message_context", "merge_home_staging_compaction")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
