"""Retain legacy catalog evidence and store new observations by launch context."""

from alembic import op

revision = "scoped_model_catalogs"
down_revision = "merge_mac_vps_video"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("""CREATE TABLE scoped_model_catalog_observations (
        observation_id TEXT NOT NULL,
        observation_revision TEXT NOT NULL,
        context_ref TEXT NOT NULL,
        observed_at INTEGER NOT NULL CHECK(observed_at >= 0),
        metadata_json TEXT NOT NULL,
        PRIMARY KEY(observation_id, observation_revision)
    )""")
    bind.exec_driver_sql("""CREATE INDEX scoped_model_catalog_context
        ON scoped_model_catalog_observations(context_ref, observed_at DESC)""")
    bind.exec_driver_sql("""CREATE TABLE scoped_model_catalog_rows (
        observation_id TEXT NOT NULL,
        observation_revision TEXT NOT NULL,
        row_ordinal INTEGER NOT NULL CHECK(row_ordinal >= 0),
        row_json TEXT NOT NULL,
        PRIMARY KEY(observation_id, observation_revision, row_ordinal),
        FOREIGN KEY(observation_id, observation_revision)
            REFERENCES scoped_model_catalog_observations(observation_id, observation_revision)
    )""")
    # Legacy values, timestamps, and absent provenance remain exactly as stored.


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
