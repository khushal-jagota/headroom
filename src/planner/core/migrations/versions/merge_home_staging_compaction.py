"""Join the home setup and staging compaction migration histories."""

revision = "merge_home_staging_compaction"
down_revision = ("linux_executor_platform", "working_turn_compaction_boundary")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
