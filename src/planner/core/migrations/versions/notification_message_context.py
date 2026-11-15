"""Keep the source notification context and native reply destination with queued work."""

from alembic import op

revision = "notification_message_context"
down_revision = "native_shared_decision_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notification_attention_edges ADD COLUMN context_json TEXT")
    op.execute("ALTER TABLE notification_apns_deliveries ADD COLUMN reply_json TEXT")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
