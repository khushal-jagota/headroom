"""Give conversation lifecycle facts a durable source without restoring capability."""

from __future__ import annotations

from alembic import op

revision = "durable_conversation_facts"
down_revision = "scheduled_ticket_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "CREATE TABLE conversation_lifecycle_facts ("
        "conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),"
        "kind TEXT NOT NULL CHECK(kind IN ('turn','attempt','request')),"
        "identity TEXT NOT NULL, turn_number INTEGER NOT NULL CHECK(turn_number > 0),"
        "operation TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN "
        "('intent','running','pending','answering','accepted','refused','unknown',"
        "'completed','failed','interrupted','unavailable','answered','started','injected')),"
        "provider_identity TEXT, PRIMARY KEY(conversation_id,kind,identity))"
    )
    # Notebook rows prove historical outcomes, but cannot establish a live turn or
    # recover provider turn identities. Preserve those rows without inventing facts.


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE conversation_lifecycle_facts")
