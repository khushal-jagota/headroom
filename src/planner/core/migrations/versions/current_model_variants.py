"""Keep successful per-variant catalogue resolutions and remove the pinned trigger.

Revision ID: current_model_variants
"""

from alembic import op

revision = "current_model_variants"
down_revision = "native_usage_prices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("""CREATE TABLE model_variant_resolutions (
        backend_key TEXT NOT NULL,
        variant TEXT NOT NULL,
        model TEXT NOT NULL,
        supported_reasoning_efforts TEXT NOT NULL,
        observed_at INTEGER NOT NULL,
        PRIMARY KEY (backend_key, variant)
    )""")
    conn.exec_driver_sql("""CREATE TABLE model_catalogue_models (
        backend_key TEXT NOT NULL,
        variant TEXT NOT NULL,
        model TEXT NOT NULL,
        supported_reasoning_efforts TEXT NOT NULL,
        observed_at INTEGER NOT NULL,
        resolved_model_id TEXT,
        PRIMARY KEY (backend_key, model)
    )""")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS sprint_items_supervisor_create")
    # Existing identities and saved settings are intentionally retained. New writes
    # explicitly select a variant, whose concrete version is resolved at dispatch.


def downgrade() -> None:
    raise RuntimeError("current model variant cache migration is forward-only")
