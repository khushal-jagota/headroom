"""Address each Ticket proposal to the principal that holds its ceiling.

Revision ID: proposal_holder
Revises: automatic_compaction_attempts
"""

from __future__ import annotations

from alembic import op

revision = "proposal_holder"
down_revision = "automatic_compaction_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tickets ADD COLUMN ceiling_holder TEXT NOT NULL "
        "DEFAULT '{\"id\":\"owner\",\"kind\":\"owner\"}' "
        "CHECK (COALESCE(json_type(ceiling_holder) = 'object' "
        "AND json_type(ceiling_holder, '$.kind') = 'text' "
        "AND json_type(ceiling_holder, '$.id') = 'text' "
        "AND json_extract(ceiling_holder, '$.kind') "
        "IN ('owner','chief','sprint_item','ticket') "
        "AND length(trim(json_extract(ceiling_holder, '$.id'))) > 0 "
        "AND json_extract(ceiling_holder, '$.id') = "
        "trim(json_extract(ceiling_holder, '$.id')) "
        "AND (json_extract(ceiling_holder, '$.kind') != 'owner' "
        "OR json_extract(ceiling_holder, '$.id') = 'owner') "
        "AND (json_extract(ceiling_holder, '$.kind') != 'chief' "
        "OR json_extract(ceiling_holder, '$.id') = 'chief'), 0))"
    )


def downgrade() -> None:
    raise NotImplementedError("proposal holder addresses cannot be discarded")
