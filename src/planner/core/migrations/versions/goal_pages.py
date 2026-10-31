"""Persist goal facts and accepted page-kit documents."""

from alembic import op

revision = "goal_pages"
down_revision = "automatic_project_measures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE goals (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
      title TEXT NOT NULL, why TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('binary','quantitative')),
      active INTEGER NOT NULL CHECK(active IN (0,1)), completed INTEGER NOT NULL CHECK(completed IN (0,1)),
      deadline TEXT, primary_chart TEXT CHECK(primary_chart IS NULL OR json_valid(primary_chart)),
      page TEXT NOT NULL CHECK(json_valid(page)), revision INTEGER NOT NULL CHECK(revision>0),
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      employee_backend TEXT NOT NULL CHECK(employee_backend IN ('codex','claude','hermes')),
      employee_launch_model TEXT NOT NULL, employee_launch_reasoning_effort TEXT)""")
    op.execute("CREATE INDEX goals_active ON goals(active,created_at,id)")
    for operation in ("INSERT", "UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER resource_goals_{operation.lower()}
        AFTER {operation} ON goals BEGIN
        INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES('goals','all',1)
        ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END""")


def downgrade() -> None:
    op.execute("DROP TABLE goals")
