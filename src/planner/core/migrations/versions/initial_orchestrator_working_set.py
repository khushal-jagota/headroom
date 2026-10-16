"""Apply the owner's exact two-active initial ordinary orchestrator set.

Revision ID: initial_orchestrator_working_set
Revises: ticket_backlog
"""

from __future__ import annotations

from alembic import op

revision = "initial_orchestrator_working_set"
down_revision = "ticket_backlog"
branch_labels = None
depends_on = None

# Titles deliberately bind the owner's selected identities. A changed or partial
# inventory needs review instead of guessing a replacement by title or position.
ACTIVE_ITEMS = {
    "si_dmhx1u7h": "Rethink how Panels works for us",
    "si_8nbv749p": "Make Panels run work across the Mac and the VPS",
}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.exec_driver_sql("SELECT id,title FROM sprint_items").mappings().all()
    if not rows:
        # A fresh install has no legacy inventory to settle. Later creation keeps
        # the ordinary product default instead of inheriting this one-time cutover.
        return
    inventory = {row["id"]: row["title"] for row in rows}
    if any(inventory.get(item_id) != title for item_id, title in ACTIVE_ITEMS.items()):
        raise RuntimeError("initial orchestrator identity changed; review exact active set")
    if any(list(inventory.values()).count(title) != 1 for title in ACTIVE_ITEMS.values()):
        raise RuntimeError("initial orchestrator title is ambiguous; review exact active set")
    # Chief is not a sprint_items row. Child Tickets and their execution state,
    # assignments, history and approval holders are deliberately untouched.
    conn.exec_driver_sql(
        "UPDATE sprint_items SET settled=CASE WHEN id IN (?,?) THEN 0 ELSE 1 END",
        tuple(ACTIVE_ITEMS),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Initial settlement is forward-only; restore the verified pre-cutover backup instead"
    )
