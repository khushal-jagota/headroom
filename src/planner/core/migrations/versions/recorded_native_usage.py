"""Store usage-only native projections and verified file cursors.

Revision ID: recorded_native_usage
Revises: disable_retired_worker_schedules
"""

from alembic import op

revision = "recorded_native_usage"
down_revision = "disable_retired_worker_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("""CREATE TABLE native_usage_sources (
        provider TEXT PRIMARY KEY CHECK(provider IN ('codex','claude')),
        source_key TEXT NOT NULL,
        snapshot TEXT NOT NULL
    )""")
    conn.exec_driver_sql("""CREATE TABLE native_usage_files (
        provider TEXT NOT NULL CHECK(provider IN ('codex','claude')),
        source_key TEXT NOT NULL,
        file_key TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        PRIMARY KEY(provider,source_key,file_key)
    )""")


def downgrade() -> None:
    raise RuntimeError("Recorded native usage is forward-only")
