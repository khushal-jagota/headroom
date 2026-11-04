"""Reserve video bytes and retain durable send admission identity."""

from alembic import op

revision = "video_upload_ledger"
down_revision = "owner_message_preview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("""CREATE TABLE conversation_video_uploads (
        upload_id TEXT PRIMARY KEY,
        destination_json TEXT NOT NULL,
        filename TEXT NOT NULL,
        byte_count INTEGER NOT NULL CHECK(byte_count > 0),
        received_bytes INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        media_type TEXT,
        expires_at INTEGER NOT NULL,
        error TEXT,
        sender_message_id TEXT,
        conversation_id TEXT,
        stored_file_id TEXT NOT NULL,
        outcome TEXT
    )""")


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
