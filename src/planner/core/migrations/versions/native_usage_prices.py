"""Own the complete external model price document for offline Usage summaries.

Revision ID: native_usage_prices
Revises: recorded_native_usage
"""

from alembic import op

revision = "native_usage_prices"
down_revision = "recorded_native_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("""CREATE TABLE native_usage_prices (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        document TEXT NOT NULL
    )""")


def downgrade() -> None:
    raise RuntimeError("Owned native usage prices are forward-only")
