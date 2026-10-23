"""Register native devices and queue APNs independently of browser delivery."""

from alembic import op

revision = "native_push_deliveries"
down_revision = "notice_suppression_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notification_apns_devices (
            device_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL CHECK(owner_id = 'owner'),
            device_token TEXT NOT NULL,
            bundle_id TEXT NOT NULL,
            environment TEXT NOT NULL CHECK(environment IN ('development','production')),
            registration_version INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(owner_id, device_token, bundle_id, environment)
        )
    """)
    op.execute("""
        CREATE TABLE notification_apns_deliveries (
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            generation INTEGER NOT NULL,
            device_id TEXT NOT NULL REFERENCES notification_apns_devices(device_id) ON DELETE CASCADE,
            title TEXT NOT NULL, body TEXT NOT NULL, route TEXT NOT NULL, tag TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','retry','delivered','failed','expired')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL,
            delivered_at INTEGER,
            last_error TEXT,
            PRIMARY KEY(subject_kind, subject_id, notification_type, generation, device_id),
            FOREIGN KEY(subject_kind, subject_id, notification_type, generation)
                REFERENCES notification_attention_edges(subject_kind, subject_id, notification_type, generation)
                ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX idx_notification_apns_due ON notification_apns_deliveries(status, next_attempt_at)")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
