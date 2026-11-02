"""Persist prospective Manager launch choices without changing existing Managers."""

from alembic import op

revision = "manager_launch_defaults"
down_revision = "ticket_integrity_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE manager_launch_defaults ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
        "employee_backend TEXT NOT NULL, employee_launch_model TEXT NOT NULL, "
        "employee_launch_reasoning_effort TEXT)"
    )
    op.execute("INSERT INTO manager_launch_defaults VALUES (1,'codex','gpt-6.1-sol','medium')")


def downgrade() -> None:
    op.execute("DROP TABLE manager_launch_defaults")
