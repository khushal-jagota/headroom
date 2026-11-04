"""Retain Mac contact and version refusal independently of live admission."""

from alembic import op

revision = "mac_contact_status"
down_revision = "conversation_background_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE mac_contact_status (singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_contact_at_ms INTEGER NOT NULL, refusal_json TEXT)"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
