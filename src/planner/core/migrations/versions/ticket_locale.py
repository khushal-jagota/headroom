"""A Ticket's execution locale and creation-time Mac requirements."""

from alembic import op

revision = "ticket_locale"
down_revision = "disable_retired_worker_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "ALTER TABLE tickets ADD COLUMN locale TEXT NOT NULL DEFAULT 'vps' "
        "CHECK (locale IN ('vps','macos'))"
    )
    for table in ("projects", "worker_types"):
        bind.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN requires_macos INTEGER NOT NULL DEFAULT 0 "
            "CHECK (requires_macos IN (0,1))"
        )


def downgrade() -> None:
    raise RuntimeError("Ticket locale is forward-only")
