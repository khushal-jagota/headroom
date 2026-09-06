"""Replace sprint prose subfields with three documents, preserving the primary bet.

Revision ID: sprint_documents
Revises: planning_day_direction
"""

from __future__ import annotations

from alembic import op

revision = "sprint_documents"
down_revision = "planning_day_direction"
branch_labels = None
depends_on = None

# Frozen historical headings: migrations must not follow future runtime definitions.
_DOCUMENT_SECTIONS = {
    "kickoff": (
        ("limiting_factor", "Limiting factor"),
        ("supports", "Supports"),
        ("premortem", "Premortem"),
    ),
    "checkpoint": (
        ("mid_where_we_stand", "Where we stand"),
        ("mid_whats_changed", "What's changed"),
        ("mid_what_to_adjust", "What to adjust"),
    ),
    "review": (
        ("outcomes", "Outcomes"),
        ("solo_reflection", "Solo reflection"),
        ("joint_discussion", "Joint discussion"),
        ("updates_to_thinking", "Updates to thinking"),
        ("carry_forward", "Carry forward"),
    ),
}


def upgrade() -> None:
    connection = op.get_bind()
    for document in _DOCUMENT_SECTIONS:
        op.execute(f"ALTER TABLE sprints ADD COLUMN {document} TEXT NOT NULL DEFAULT ''")
    rows = connection.exec_driver_sql("SELECT * FROM sprints ORDER BY id").mappings().all()
    for row in rows:
        documents = [
            "\n\n".join(
                f"## {heading}\n\n{row[field]}"
                for field, heading in sections
                if row[field] != ""
            )
            for sections in _DOCUMENT_SECTIONS.values()
        ]
        connection.exec_driver_sql(
            "UPDATE sprints SET kickoff = ?, checkpoint = ?, review = ? WHERE id = ?",
            (*documents, row["id"]),
        )
    for sections in _DOCUMENT_SECTIONS.values():
        for field, _heading in sections:
            op.execute(f"ALTER TABLE sprints DROP COLUMN {field}")


def downgrade() -> None:
    raise NotImplementedError("document edits cannot be split back into retired sprint fields")
