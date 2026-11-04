"""Remove unused records of the skills selected for Worker steps."""

from alembic import op

revision = "remove_worker_step_skill_bindings"
down_revision = "manager_launch_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("worker_step_skill_bindings")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
