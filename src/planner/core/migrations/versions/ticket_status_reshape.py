"""Ticket statuses reshaped to eight: three renamed, one merged away, and blocked derived.

Revision ID: ticket_status_reshape
Revises: baseline_v37
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)

revision = "ticket_status_reshape"
down_revision = "baseline_v37"
branch_labels = None
depends_on = None

# The eight statuses this revision leaves behind.
FINAL_TICKET_STATUSES = (
    "'empty','blocked','agent','paired','awaiting_approval','needs_user','user','errored'"
)

# The old names and the new ones admitted at once, for the length of one rebuild. The rows
# cannot move first: the old table's CHECK rejects every new name. The CHECK cannot change
# without rebuilding the table, and the copy a rebuild does evaluates the new CHECK against
# the old rows, so the value both sides accept is a CHECK that admits both sets.
TRANSITIONAL_TICKET_STATUSES = (
    FINAL_TICKET_STATUSES
    + ",'agent_running_step','paired_work','user_takeover','proposal_discussion'"
)

# Old value -> new value. proposal_discussion is not renamed but merged: the state it named
# gets no machinery of its own any more, and paired is where those tickets belong.
TICKET_STATUS_RENAMES: tuple[tuple[str, str], ...] = (
    ("agent_running_step", "agent"),
    ("paired_work", "paired"),
    ("user_takeover", "user"),
    ("proposal_discussion", "paired"),
)

# blocked is empty's stand-in: a ticket that has come to rest and cannot be picked up
# because something live still blocks it. Live is the rule core/links.py already uses — a
# blocks link whose source ticket is not done or dropped. There is no condition on the
# blocked ticket's own stage, because the rule has none.
DERIVE_BLOCKED = """
UPDATE tickets SET ticket_status = 'blocked'
 WHERE ticket_status = 'empty'
   AND id IN (
     SELECT l.to_id FROM links l
       JOIN tickets src ON src.id = l.from_id
      WHERE l.kind = 'blocks' AND src.stage NOT IN ('done','dropped')
   )
"""


def tickets_table(ticket_statuses: str) -> Table:
    """`tickets` declared in full, because a rebuild recreates exactly what it is handed.

    Everything left out is dropped and nothing is left dangling to notice it by: the
    outgoing foreign keys, all five CHECK constraints, and every index — including the
    unique one on alias, which is correctness rather than speed.
    """
    table = Table(
        "tickets",
        MetaData(),
        # Nullable, which is not a typo: SQLite lets a TEXT PRIMARY KEY hold NULL, and the
        # baseline's `id TEXT PRIMARY KEY` is a column without NOT NULL on it. Declaring it
        # the way SQLAlchemy would by default adds a constraint this revision never said it
        # was adding, and this revision changes a CHECK and nothing else.
        Column("id", Text, primary_key=True, nullable=True),
        Column("title", Text, nullable=False),
        Column("worker_type", Text, nullable=False),
        Column("employee_backend", Text, nullable=False),
        Column("employee_launch_model", Text),
        Column("employee_launch_reasoning_effort", Text),
        Column("stage", Text, nullable=False, server_default=text("'needs_kickoff'")),
        Column("priority", Text, nullable=False, server_default=text("'P3'")),
        Column("deadline", Text),
        Column("project_id", Text, ForeignKey("projects.id")),
        Column("sprint_item_id", Text, ForeignKey("sprint_items.id")),
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        Column("recap", Text, nullable=False, server_default=text("''")),
        Column("ceiling", Text, nullable=False),
        Column("at_cap", Text, nullable=False, server_default=text("'propose'")),
        Column("ticket_status", Text, nullable=False, server_default=text("'empty'")),
        Column("backend_error", Text),
        Column("stage_ownership_overrides", Text, nullable=False, server_default=text("'{}'")),
        Column("default_stage_ownership_mode", Text),
        Column("employee_session_id", Text),
        Column("alias", Text),
        Column("fields", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        CheckConstraint("at_cap IN ('stop','propose')"),
        CheckConstraint(f"ticket_status IN ({ticket_statuses})"),
        CheckConstraint("default_stage_ownership_mode IN ('worker','user','paired')"),
    )
    Index("idx_tickets_alias", table.c.alias, unique=True, sqlite_where=text("alias IS NOT NULL"))
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table


def upgrade() -> None:
    # Widen the CHECK, move the rows, then narrow it. Two rebuilds, because the CHECK that
    # has to let the old rows through is not the CHECK this revision is for.
    with op.batch_alter_table(
        "tickets", copy_from=tickets_table(TRANSITIONAL_TICKET_STATUSES), recreate="always"
    ):
        pass

    for old_status, new_status in TICKET_STATUS_RENAMES:
        op.execute(
            f"UPDATE tickets SET ticket_status = '{new_status}' "
            f"WHERE ticket_status = '{old_status}'"
        )
    op.execute(DERIVE_BLOCKED)

    with op.batch_alter_table(
        "tickets", copy_from=tickets_table(FINAL_TICKET_STATUSES), recreate="always"
    ):
        pass


def downgrade() -> None:
    raise NotImplementedError(
        "two old statuses map onto paired and blocked was derived, so there is no going back"
    )
