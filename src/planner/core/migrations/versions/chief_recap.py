"""Give the Chief an owner-editable recap without changing its launch or skill."""

from alembic import op

revision = "chief_recap"
down_revision = "remove_ticket_retirement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE chief_settings ADD COLUMN recap TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
