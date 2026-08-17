"""Drop the watch flag and the ping. A Sprint Item conversation now waits to be spoken to.

Three columns carried the two mechanisms that reached the user without the user asking.
``tickets.wakes_supervisor`` chose which Tickets woke an Item's conversation, and the two
``sprint_items`` ping columns held where that conversation last asked for the reader.
Nothing writes or reads any of them now, so they go.

An undecided ping fact is deleted with them. The notification loop decides a fact once,
and ``sprint_item_ping`` is no longer a type it knows, so a fact still waiting for its
decision at upgrade time would stop the loop. A decided one is finished business and
stays: history is durable.

The notification tables keep ``sprint_item_ping`` and ``sprint_item`` in their CHECK
lists. Removing a value from a SQLite CHECK means rebuilding the table, and these five
are chained by foreign keys, so tightening them would put every past ping notification
at risk to forbid a value nothing writes.

Revision ID: drop_supervisor_wake_and_ping
Revises: ticket_wakes_supervisor
"""

from __future__ import annotations

from alembic import op

revision = "drop_supervisor_wake_and_ping"
down_revision = "ticket_wakes_supervisor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM notification_facts WHERE notification_type = 'sprint_item_ping' "
        "AND fact_id NOT IN (SELECT fact_id FROM notification_decisions)"
    )
    op.execute(
        "DELETE FROM notification_preferences "
        "WHERE notification_type = 'sprint_item_ping'"
    )
    op.execute("DELETE FROM notification_projection_cursors WHERE source_kind = 'sprint_item_ping'")
    op.execute("ALTER TABLE tickets DROP COLUMN wakes_supervisor")
    op.execute("ALTER TABLE sprint_items DROP COLUMN supervisor_ping_sequence")
    op.execute("ALTER TABLE sprint_items DROP COLUMN supervisor_ping_at")
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Supervisor wake and ping drop failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("the wake and the ping are not coming back")
