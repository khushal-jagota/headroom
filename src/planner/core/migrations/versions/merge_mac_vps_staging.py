"""Join the reviewed Mac workspace history with current staging without rewriting either."""

revision = "merge_mac_vps_staging"
down_revision = ("conversation_workspace_policy", "owner_message_preview")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Mac/VPS staging reconciliation is forward-only")
