"""Replace generic links with the explicit Ticket-blocks-Ticket relationship.

Revision ID: ticket_blocks
Revises: proposal_delivery_failures
"""

from __future__ import annotations

from alembic import op

revision = "ticket_blocks"
down_revision = "proposal_delivery_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    invalid_rows = connection.exec_driver_sql(
        """
        SELECT links.from_id, links.to_id, links.kind
        FROM links
        LEFT JOIN tickets blocker ON blocker.id = links.from_id
        LEFT JOIN tickets blocked ON blocked.id = links.to_id
        WHERE links.kind IS NOT 'blocks'
           OR blocker.id IS NULL
           OR blocked.id IS NULL
           OR links.from_id = links.to_id
        ORDER BY links.from_id, links.to_id, links.kind
        """
    ).fetchall()
    if invalid_rows:
        raise RuntimeError(
            "cannot migrate links to ticket_blocks: every legacy row must be a blocks "
            f"row between two existing Tickets; invalid rows: {invalid_rows!r}"
        )

    op.execute(
        "CREATE TABLE ticket_blocks ("
        "blocking_ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,"
        "blocked_ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,"
        "PRIMARY KEY(blocking_ticket_id,blocked_ticket_id),"
        "CHECK(blocking_ticket_id <> blocked_ticket_id))"
    )
    op.execute(
        "INSERT INTO ticket_blocks (blocking_ticket_id,blocked_ticket_id) "
        "SELECT from_id,to_id FROM links"
    )

    legacy_count = int(connection.exec_driver_sql("SELECT count(*) FROM links").scalar_one())
    copied_count = int(
        connection.exec_driver_sql("SELECT count(*) FROM ticket_blocks").scalar_one()
    )
    missing_copies = connection.exec_driver_sql(
        """
        SELECT links.from_id, links.to_id
        FROM links
        WHERE NOT EXISTS (
          SELECT 1 FROM ticket_blocks
          WHERE blocking_ticket_id = links.from_id
            AND blocked_ticket_id = links.to_id
        )
        """
    ).fetchall()
    extra_copies = connection.exec_driver_sql(
        """
        SELECT ticket_blocks.blocking_ticket_id, ticket_blocks.blocked_ticket_id
        FROM ticket_blocks
        WHERE NOT EXISTS (
          SELECT 1 FROM links
          WHERE from_id = ticket_blocks.blocking_ticket_id
            AND to_id = ticket_blocks.blocked_ticket_id
        )
        """
    ).fetchall()
    if legacy_count != copied_count or missing_copies or extra_copies:
        raise RuntimeError(
            "cannot migrate links to ticket_blocks: copied Ticket pairs did not reconcile"
        )

    op.execute(
        "CREATE INDEX idx_ticket_blocks_blocked_ticket_id "
        "ON ticket_blocks(blocked_ticket_id)"
    )
    op.execute("DROP TABLE links")


def downgrade() -> None:
    raise NotImplementedError("the generic links model cannot be restored")
