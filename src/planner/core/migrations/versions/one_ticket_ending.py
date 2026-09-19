"""Remove judgments, and leave a Ticket one ending.

Revision ID: one_ticket_ending
Revises: worker_types_in_database

Judgments were rated on 1 Ticket out of 890 and no trouble note ever had text, so both
tables go. `dropped` was reached by 2 Tickets and cost every caller a second terminal to
know about, so those Tickets become done and the stage stops existing. A stored Worker
type carries its stages as JSON and is read back against an exact set of keys, so the
`dropped` key must leave the row as well as the code.
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op

revision = "one_ticket_ending"
down_revision = "worker_types_in_database"
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

    row = conn.exec_driver_sql(
        "SELECT source_text FROM managed_skills WHERE skill_name = 'panels-worker'"
    ).fetchone()
    if row is not None and TROUBLE_BULLET in row[0]:
        conn.exec_driver_sql(
            "UPDATE managed_skills SET source_text = ? WHERE skill_name = 'panels-worker'",
            (row[0].replace(TROUBLE_BULLET, ""),),
        )


def downgrade() -> None:
    raise NotImplementedError("discarded judgments and dropped Tickets cannot be reconstructed")
