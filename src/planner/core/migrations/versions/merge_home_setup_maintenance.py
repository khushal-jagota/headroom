"""Join home setup and maintenance-turn migration histories."""

revision = "merge_home_setup_maintenance"
down_revision = ("automatic_start_lookup", "memory_notice_turns_are_maintenance")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
