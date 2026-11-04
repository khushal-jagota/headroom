"""Remove Project and Worker type requirements without changing stored locales."""

from alembic import op

revision = "remove_macos_requirements"
down_revision = "manager_launch_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("projects", "worker_types"):
        bind.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN requires_macos")


def downgrade() -> None:
    raise RuntimeError("Mac requirement removal is forward-only")
