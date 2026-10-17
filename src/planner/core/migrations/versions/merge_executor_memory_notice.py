"""Join executor acceptance and automatic memory notice authorization histories."""

revision = "merge_executor_memory_notice"
down_revision = ("executor_authoritative_acceptance", "consume_memory_notice_authorization")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both independent parents retain their original schema and cutover behavior.
    pass


def downgrade() -> None:
    raise RuntimeError("Executor and memory notice integration is forward-only")
