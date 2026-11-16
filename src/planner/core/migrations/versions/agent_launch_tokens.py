"""Persist only the hash of each agent conversation's launch credential."""

from alembic import op

revision = "agent_launch_tokens"
down_revision = "proposal_before_errored_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE agent_launch_tokens (token_hash TEXT PRIMARY KEY, "
        "conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(conversation_id) "
        "ON DELETE CASCADE)"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
