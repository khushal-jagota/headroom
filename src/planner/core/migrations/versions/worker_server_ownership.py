"""Restore the owner-approved server rule without changing other skill text."""

from alembic import op

from planner.core.migrations.server_ownership_correction_data import SERVER_OWNERSHIP_SECTION

revision = "worker_server_ownership"
down_revision = "workspace_content_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.exec_driver_sql(
        "SELECT source_text FROM managed_skills WHERE skill_name = ?", ("panels-worker",)
    ).fetchone()
    if row is None or SERVER_OWNERSHIP_SECTION in row[0]:
        return
    conn.exec_driver_sql(
        "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
        (row[0] + "\n\n" + SERVER_OWNERSHIP_SECTION, "panels-worker"),
    )


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
