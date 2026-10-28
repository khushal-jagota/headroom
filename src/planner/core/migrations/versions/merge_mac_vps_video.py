"""Join repaired Mac/VPS history and the retained video upload ledger."""

revision = "merge_mac_vps_video"
down_revision = ("merge_mac_vps_staging", "video_upload_ledger")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Mac/VPS and video reconciliation is forward-only")
