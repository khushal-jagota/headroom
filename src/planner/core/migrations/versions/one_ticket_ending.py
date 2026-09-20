"""Remove judgments, and leave a Ticket one ending.

Revision ID: one_ticket_ending
Revises: drop_ticket_archived_field_content

Judgments were rated on 1 Ticket out of 890 and no trouble note ever had text, so both
tables go. `dropped` was reached by 2 Tickets and cost every caller a second terminal to
know about, so those Tickets become done and the stage stops existing. A stored Worker
type carries its stages as JSON and is read back against an exact set of keys, so the
`dropped` key must leave the row as well as the code. The skill rows teaching the stage
are corrected here too, because the packaged tree is only a seed and the row is what a
Worker actually reads.
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op

revision = "one_ticket_ending"
down_revision = "drop_ticket_archived_field_content"
branch_labels = None
depends_on = None

# The bullet that told a worker to use `panels worker trouble`. The command goes with the
# judgments API it posted to. A skill row is the owner's to edit, so this is removed only
# where it still reads exactly as shipped.
TROUBLE_BULLET = """- **`panels worker trouble`**, piping the note on stdin — record one short trouble note on your
  current Ticket during the active claimed worker step. Use it for a harness, tool, or
  Ticket problem that did not go well. Record only trouble that you encountered. Do not
  grade yourself or record what went well.
"""


# The line every stage list used to end on, and the places `panels-worker-new-worker` told
# a worker to name the stage — including in the `panels worker-type save` record, which
# now refuses the key. Each is replaced only where the row still reads exactly as shipped.
STAGE_LINE = "- **dropped** — abandoned.\n"

SKILL_REPLACEMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "panels-worker": ((TROUBLE_BULLET, ""),),
    "panels-worker-coding": ((STAGE_LINE, ""),),
    "panels-worker-research": ((STAGE_LINE, ""),),
    "panels-worker-exploration": ((STAGE_LINE, ""),),
    "panels-worker-planning-sprint": ((STAGE_LINE, ""),),
    "panels-worker-initiative-planning": ((STAGE_LINE, ""),),
    "panels-worker-initiative-review": ((STAGE_LINE, ""),),
    "panels-chief-of-staff": (
        (
            "`--include-terminal` only when finished or dropped work is relevant.",
            "`--include-terminal` only when finished work is relevant.",
        ),
    ),
    "panels-worker-new-worker": (
        (
            "(`needs_kickoff`, `done`, and `dropped` are the universal bookends every "
            "worker shares.)",
            "(`needs_kickoff` and `done` are the universal bookends every worker shares.)",
        ),
        (
            "Terminal `done` and `dropped` have no ownership mode.",
            "Terminal `done` has no ownership mode.",
        ),
        (
            "reuse the shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, "
            "`done`, `dropped`.",
            "reuse the shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, "
            "and `done`.",
        ),
        (
            "`worker_type`, `label`, `stages`, `dropped`, `fields`, `profile`",
            "`worker_type`, `label`, `stages`, `fields`, `profile`",
        ),
    ),
}


def upgrade() -> None:
    conn = op.get_bind()

    op.drop_table("ticket_judgment_trouble_notes")
    op.drop_table("ticket_judgments")

    conn.exec_driver_sql("UPDATE tickets SET stage = 'done' WHERE stage = 'dropped'")

    for worker_type, definition_json in conn.exec_driver_sql(
        "SELECT worker_type, definition_json FROM worker_types"
    ).fetchall():
        decoded: Any = json.loads(definition_json)
        if not isinstance(decoded, dict) or "dropped" not in decoded:
            continue
        del decoded["dropped"]
        conn.exec_driver_sql(
            "UPDATE worker_types SET definition_json = ? WHERE worker_type = ?",
            (json.dumps(decoded, ensure_ascii=False), worker_type),
        )

    for skill_name, replacements in SKILL_REPLACEMENTS.items():
        row = conn.exec_driver_sql(
            "SELECT source_text FROM managed_skills WHERE skill_name = ?", (skill_name,)
        ).fetchone()
        if row is None:
            continue
        source_text = str(row[0])
        corrected = source_text
        for old_text, new_text in replacements:
            corrected = corrected.replace(old_text, new_text)
        if corrected != source_text:
            conn.exec_driver_sql(
                "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
                (corrected, skill_name),
            )


def downgrade() -> None:
    raise NotImplementedError("discarded judgments and dropped Tickets cannot be reconstructed")
