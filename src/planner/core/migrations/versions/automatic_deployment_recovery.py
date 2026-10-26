"""Track only the exact claim rearmed by an interrupted-turn delivery."""

from alembic import op

revision = "automatic_deployment_recovery"
down_revision = "notice_suppression_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deploy_interruption_deliveries ADD COLUMN restored_claim_revision INTEGER"
    )

    op.execute(
        "ALTER TABLE deploy_interruption_deliveries ADD COLUMN recovery_sender_message_id TEXT"
    )


def downgrade() -> None:
    op.drop_column("deploy_interruption_deliveries", "recovery_sender_message_id")
    op.drop_column("deploy_interruption_deliveries", "restored_claim_revision")
