"""Move stored Planning Day Tickets to the review and direction workflow.

Revision ID: planning_day_direction
Revises: conversation_automatic_compaction
"""

from __future__ import annotations

import json

from alembic import op

revision = "planning_day_direction"
down_revision = "conversation_automatic_compaction"
branch_labels = None
depends_on = None

_EMPTY_SLOT = {"value": None, "proposal": None, "user_note": None}
_STAGE_REPLACEMENTS = {
    "needs_kickoff": "needs_review",
    "needs_gather": "needs_review",
    "needs_planning": "needs_direction",
}


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        "SELECT id, stage, ceiling, fields FROM tickets "
        "WHERE worker_type = 'planning-day' ORDER BY id"
    ).mappings()
    for row in rows:
        fields = json.loads(str(row["fields"]))
        for field in ("review", "direction", "day_changes", "closeout"):
            fields.setdefault(field, dict(_EMPTY_SLOT))

        old_stage = str(row["stage"])
        new_stage = _STAGE_REPLACEMENTS.get(old_stage, old_stage)
        old_ceiling = str(row["ceiling"])
        new_ceiling = _STAGE_REPLACEMENTS.get(old_ceiling, old_ceiling)
        status = None
        ownership = None
        if old_stage in _STAGE_REPLACEMENTS:
            status = "paired" if new_stage == "needs_direction" else "empty"
            ownership = "paired" if new_stage == "needs_direction" else "worker"

        connection.exec_driver_sql(
            "UPDATE tickets SET stage = ?, ceiling = ?, fields = ?, "
            "ticket_status = COALESCE(?, ticket_status), "
            "default_stage_ownership_mode = COALESCE(?, default_stage_ownership_mode) "
            "WHERE id = ?",
            (
                new_stage,
                new_ceiling,
                json.dumps(fields, separators=(",", ":")),
                status,
                ownership,
                str(row["id"]),
            ),
        )


def downgrade() -> None:
    raise NotImplementedError("the removed Planning Day stages cannot be reconstructed")
