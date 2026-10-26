"""Separate what an orchestrator is for from what is happening in it right now.

The rename carries the existing text with the name, so the brief every orchestrator
was created with becomes its goal without a row being read or rewritten. The recap
starts empty for its supervisor to fill.
"""

from alembic import op

revision = "goal_and_recap_for_orchestrators"
down_revision = "ticket_field_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE sprint_items RENAME COLUMN body TO goal")
    bind.exec_driver_sql("ALTER TABLE sprint_items ADD COLUMN recap TEXT NOT NULL DEFAULT ''")
    # Nothing here touches the navigation projection. An earlier revision in this chain
    # already wrote every orchestrator record, taking the recap from the only column
    # there was, so those stored records hold a goal under the name recap until they are
    # rebuilt. Publication installation rebuilds them before it serves anything, and a
    # migration that rebuilt them here would change the epoch during mere preparation.


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
