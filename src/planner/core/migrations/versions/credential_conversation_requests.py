"""Keep credential requests and their nonsecret delivery attempts beside conversations."""

from alembic import op

revision = "credential_conversation_requests"
down_revision = "reusable_named_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE credential_requests (
        request_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        requester_kind TEXT NOT NULL, requester_id TEXT NOT NULL,
        request_json TEXT NOT NULL, body TEXT NOT NULL,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    op.execute(
        "CREATE INDEX credential_requests_conversation ON credential_requests(conversation_id)"
    )
    op.execute("""CREATE TABLE credential_request_delivery_attempts (
        attempt_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL REFERENCES credential_requests(request_id) ON DELETE CASCADE,
        sender_message_id TEXT NOT NULL UNIQUE,
        prompt TEXT NOT NULL, status TEXT NOT NULL, refusal_reason TEXT,
        created_at INTEGER NOT NULL
    )""")
    op.execute(
        "CREATE INDEX credential_request_attempts_request ON "
        "credential_request_delivery_attempts(request_id,created_at)"
    )


def downgrade() -> None:
    raise RuntimeError("Credential requests are forward-only. Restore the matching backup.")
