"""Drop the reviewer stamped on parked Ticket proposals.

Who reviews a parked proposal is derived from the Ticket's at_cap at the moment of the
decision, so the copy kept on the proposal has no reader left.

Revision ID: drop_proposal_review_route
Revises: conversation_held_prompts
"""

from __future__ import annotations

from alembic import op

revision = "drop_proposal_review_route"
down_revision = "conversation_held_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rebuild each field slot without its proposal route, preserving every other
    # property. The correlated json_each supports every registered Worker type, and
    # mirrors the json_set which added the key in ticket_review_routes.
    op.execute(
        "UPDATE tickets SET fields=(SELECT json_group_object(j.key, "
        "CASE WHEN json_type(j.value, '$.proposal')='object' "
        "THEN json_remove(j.value, '$.proposal.review_route') ELSE j.value END) "
        "FROM json_each(tickets.fields) AS j)"
    )
    remaining = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM tickets, json_each(tickets.fields) AS j "
        "WHERE json_type(j.value, '$.proposal.review_route') IS NOT NULL"
    ).scalar()
    if remaining:
        raise RuntimeError(f"proposal review route remains on {remaining} field slots")


def downgrade() -> None:
    raise NotImplementedError("a proposal reviewer cannot be recovered once derived")
