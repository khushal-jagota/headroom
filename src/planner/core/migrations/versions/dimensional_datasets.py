"""Retain joint dimensional results and current category labels."""

from alembic import op

revision = "dimensional_datasets"
# Development parent only; rebase after the checked credential/request handoff.
down_revision = "reusable_named_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    declarations = (
        """CREATE TABLE datasets (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          source_id TEXT NOT NULL REFERENCES measure_sources(id) ON DELETE RESTRICT,
          name TEXT NOT NULL, timezone TEXT NOT NULL,
          dimensions TEXT NOT NULL CHECK(json_valid(dimensions)),
          fields TEXT NOT NULL CHECK(json_valid(fields)),
          definition_revision INTEGER NOT NULL CHECK(definition_revision>0),
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
        """CREATE TABLE dataset_results (
          dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
          date TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES measure_source_runs(id) ON DELETE RESTRICT,
          definition_revision INTEGER NOT NULL CHECK(definition_revision>0),
          output TEXT NOT NULL CHECK(json_valid(output)),
          collected_at INTEGER NOT NULL, query_end_at REAL NOT NULL, query_start_at REAL,
          unavailable_reason TEXT, limitation TEXT, PRIMARY KEY(dataset_id,date))""",
        """CREATE TABLE dataset_rows (
          dataset_id TEXT NOT NULL, date TEXT NOT NULL, dimension_key TEXT NOT NULL,
          dimensions TEXT NOT NULL CHECK(json_valid(dimensions)),
          field_values TEXT NOT NULL CHECK(json_valid(field_values)),
          PRIMARY KEY(dataset_id,date,dimension_key),
          FOREIGN KEY(dataset_id,date) REFERENCES dataset_results(dataset_id,date) ON DELETE CASCADE)""",
        """CREATE TABLE dataset_category_labels (
          dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
          dimension TEXT NOT NULL, code TEXT NOT NULL, label TEXT NOT NULL,
          PRIMARY KEY(dataset_id,dimension,code))""",
        "CREATE INDEX idx_datasets_project ON datasets(project_id,id)",
        "CREATE INDEX idx_datasets_source ON datasets(source_id,id)",
    )
    for declaration in declarations:
        conn.exec_driver_sql(declaration)
    for table in ("datasets", "dataset_results", "dataset_rows", "dataset_category_labels"):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            conn.exec_driver_sql(
                f'CREATE TRIGGER "resource_measures_{table}_{operation.lower()}" '
                f"AFTER {operation} ON {table} BEGIN "
                "INSERT INTO resource_source_versions(source_kind,source_id,revision) "
                "VALUES ('automatic-measures','all',1) ON CONFLICT(source_kind,source_id) "
                "DO UPDATE SET revision=revision+1; END"
            )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
