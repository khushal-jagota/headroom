"""Restore review for unfinished Consequences work without reopening Done Tickets."""

import json
import sqlite3
from typing import cast

from alembic import op

from planner.core.migrations.versions.consequences_without_approval import (
    COMPLETION_INSTRUCTION,
    RECOVERY_INSTRUCTION,
)

revision = "restore_consequences_review"
down_revision = "proposal_before_errored_claim"
branch_labels = None
depends_on = None


_REPLACEMENTS = {
    "It settles and finishes the Ticket without another approval.": "The ceiling holder reviews it before the Ticket reaches Done.",
    "Once the authorized work is complete, its report settles without another approval. "
    "This removes a completion gate, not the explicit engagement requirement.": "Once the authorized work is complete, the ceiling holder reviews its report.",
    "Panels settles that report and finishes the Ticket without another approval.": "The ceiling holder reviews that report before the Ticket reaches Done.",
    "Automatic settlement does not grant permission for human-owned work.": "The report does not grant permission for human-owned work.",
}


def _restore_guidance(text: str) -> str:
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    for ticket_id, ceiling, workflow_json in conn.execute(
        "SELECT id,ceiling,workflow_json FROM tickets WHERE stage != 'done'"
    ).fetchall():
        workflow = json.loads(workflow_json)
        workflow["guidance"] = _restore_guidance(workflow.get("guidance", ""))
        for stage in workflow["stages"]:
            stage["instructions"] = _restore_guidance(stage.get("instructions", ""))
        completion = next(s for s in workflow["stages"] if s.get("gating_field") == "consequences")
        instructions = completion.get("instructions", "")
        instructions = instructions.replace("\n\n" + COMPLETION_INSTRUCTION, "")
        instructions = instructions.replace(
            RECOVERY_INSTRUCTION,
            "This report previously waited for Consequences approval. Check the actual result "
            "before repeating any side effect. Complete missing authorized work, then submit "
            "the verified report for review. The previous report follows:\n",
        )
        completion["instructions"] = instructions
        done = next(s["id"] for s in workflow["stages"] if s.get("is_terminal"))
        if ceiling == done:
            ceiling = completion["id"]
        conn.execute(
            "UPDATE tickets SET ceiling=?,workflow_json=? WHERE id=?",
            (ceiling, json.dumps(workflow, ensure_ascii=False), ticket_id),
        )

    # The projection stores ceiling alongside each Ticket and must match the migrated row.
    from planner.navigation.data import rebuild

    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rebuild(conn)
    finally:
        conn.row_factory = previous_factory


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
