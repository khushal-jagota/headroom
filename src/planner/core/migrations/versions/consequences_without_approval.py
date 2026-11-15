"""Return Consequences reports to their Workers for verified completion.

No report is treated as completed work on upgrade. Existing ownership, stage order,
claims, pauses, and blockers stay intact. Recovery text is carried by the Ticket's
own saved stage instructions, which the next Consequences prompt sends in full.
"""

import json
import sqlite3
from typing import cast

from alembic import op

revision = "consequences_without_approval"
down_revision = "actual_compaction_boundaries"
branch_labels = None
depends_on = None

COMPLETION_INSTRUCTION = (
    "Consequences has no approval gate. Complete only the authorized follow-through, "
    "verify its result, and submit the consequences report. The report settles automatically. "
    "Preserve real blockers, errors, pauses, and any required human work. "
    "Do not report incomplete work as Done."
)
RECOVERY_INSTRUCTION = (
    "This report was waiting for Consequences approval before the completion rule changed. "
    "Check the actual result before repeating any side effect. Complete missing authorized "
    "work, then submit the verified report without another approval. "
    "The previous report follows:\n"
)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    rows = conn.execute(
        "SELECT id,stage,workflow_json,pending_proposal FROM tickets WHERE stage != 'done'"
    ).fetchall()
    for ticket_id, current_stage, workflow_json, pending_json in rows:
        workflow = json.loads(workflow_json)
        completion = next(
            stage for stage in workflow["stages"] if stage.get("gating_field") == "consequences"
        )
        instructions = completion.get("instructions", "") + "\n\n" + COMPLETION_INSTRUCTION
        pending = None if pending_json is None else json.loads(pending_json)
        recover = (
            current_stage == completion["id"]
            and pending is not None
            and pending["field"] == "consequences"
        )
        if recover:
            assert pending is not None
            instructions += "\n\n" + RECOVERY_INSTRUCTION + pending["body"]
        completion["instructions"] = instructions
        conn.execute(
            "UPDATE tickets SET workflow_json=?,pending_proposal=? WHERE id=?",
            (
                json.dumps(workflow, ensure_ascii=False),
                None if recover else pending_json,
                ticket_id,
            ),
        )
    # The compact index stores a Ticket's derived status. Rebuild it in the same
    # transaction so parked reports disappear from Review together with their gates.
    from planner.navigation.data import rebuild

    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rebuild(conn)
    finally:
        conn.row_factory = previous_factory


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
