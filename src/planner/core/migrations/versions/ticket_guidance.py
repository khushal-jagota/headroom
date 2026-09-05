"""Preserve every historical field note in one Ticket guidance document."""

from __future__ import annotations

import json
from typing import Any

from alembic import op

revision = "ticket_guidance"
down_revision = "sprint_documents"
branch_labels = None
depends_on = None


def _converted_fields(raw: str) -> tuple[str, str]:
    fields: Any = json.loads(raw)
    if not isinstance(fields, dict):
        raise ValueError("corrupt ticket fields JSON")
    sections: list[str] = []
    for field_id, slot in fields.items():
        if not isinstance(slot, dict):
            raise ValueError("corrupt ticket field slot")
        value = slot.get("value")
        if value is not None and not isinstance(value, str):
            raise ValueError("corrupt ticket field value")
        proposal = slot.get("proposal")
        if proposal is not None and (
            not isinstance(proposal, dict)
            or not isinstance(proposal.get("body"), str)
            or not isinstance(proposal.get("proposed_by"), str)
            or not isinstance(proposal.get("created_at"), int)
            or isinstance(proposal.get("created_at"), bool)
        ):
            raise ValueError("corrupt ticket proposal")
        notes: list[tuple[str, str]] = []
        for key in ("user_note", "notes"):
            note = slot.pop(key, None)
            if note is not None and not isinstance(note, str):
                raise ValueError("corrupt ticket guidance")
            if isinstance(note, str) and note != "":
                notes.append((key, note))
        if notes:
            body = (
                notes[0][1]
                if len(notes) == 1
                else "\n\n".join(f"### {key}\n\n{note}" for key, note in notes)
            )
            sections.append(f"## {field_id}\n\n{body}")
    return json.dumps(fields, ensure_ascii=False), "\n\n".join(sections)


def upgrade() -> None:
    connection = op.get_bind()
    # Validate every row before the first schema or record write. Historical field
    # names are data; no current registry may decide which text survives.
    converted = [
        (str(row["id"]), *_converted_fields(str(row["fields"])))
        for row in connection.exec_driver_sql(
            "SELECT id, fields FROM tickets ORDER BY id"
        ).mappings()
    ]
    connection.exec_driver_sql("ALTER TABLE tickets ADD COLUMN guidance TEXT NOT NULL DEFAULT ''")
    for ticket_id, fields, guidance in converted:
        connection.exec_driver_sql(
            "UPDATE tickets SET fields = ?, guidance = ? WHERE id = ?",
            (fields, guidance, ticket_id),
        )


def downgrade() -> None:
    raise NotImplementedError("Ticket guidance cannot be split into the former field notes")
