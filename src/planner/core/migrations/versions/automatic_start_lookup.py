"""Keep exact-turn admission operations independent of retained start history."""

from alembic import op

revision = "automatic_start_lookup"
down_revision = "worker_cleanup_recheck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX automatic_start_bindings_turn "
        "ON automatic_start_bindings(conversation_id,turn_number)"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
