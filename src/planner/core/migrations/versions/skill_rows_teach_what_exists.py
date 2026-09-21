"""Correct the skill rows that still teach the stage ladder this build renamed.

Revision ID: skill_rows_teach_what_exists
Revises: skill_rows_name_what_exists

The revision before this one corrected the rows that name an absent command, Stage id
or retired skill. It proves each correction by finding the text in a version this
repository shipped, which is what stops it deleting a sentence the owner wrote. That
rule is right and this revision does not loosen it.

Six rows were out of its reach for exactly that reason. They carry the pre-rename
ladder in label form, inside the owner's own sentences: "Kickoff", "Approach",
"Closeout", "Rough Shape", "Ticket Outlines". No shipped version contains those
sentences, so no shipped-text proof can exist for them.

The authority here is narrower instead of looser. Every correction renames a Stage or a
field and keeps the sentence around it, the owner read and approved the whole set as
exact before-and-after text, and the generator refuses any span that does not occur
exactly once in the row it names.

Like the revision before it, this one refuses to fail quietly: it declares in
``LADDER_CLAIMS`` what must be gone and raises when a claim it made did not land. It
claims only names the build no longer has anywhere. "approach" is ordinary English and
"kickoff" names the live ``--kickoff-note`` option, so neither is claimed and neither
is touched.

``updated_at`` is deliberately left alone. These corrections are the build's, and
moving the timestamp would present them as the owner's own edit.
"""

from __future__ import annotations

from alembic import op

from planner.core.migrations.skill_ladder_correction_data import (
    LADDER_CLAIMS,
    LADDER_CORRECTIONS,
)

revision = "skill_rows_teach_what_exists"
down_revision = "skill_rows_name_what_exists"
branch_labels = None
depends_on = None


def corrected_sources(stored: dict[str, str]) -> dict[str, str]:
    """Apply every correction to the rows as they stand. Exported so a test can read it."""
    corrected = dict(stored)
    for skill_name, replaced_text, replacement, _why in LADDER_CORRECTIONS:
        text = corrected.get(skill_name)
        if text is None or replaced_text not in text:
            continue
        corrected[skill_name] = text.replace(replaced_text, replacement)
    return corrected


def unmet_claims(stored: dict[str, str], corrected: dict[str, str]) -> list[str]:
    """Claims this revision made against rows that held the text, and did not keep.

    A row that never held the text was never claimed, so a fresh database — where
    seeding runs after the migrations and this table is still empty — reports nothing.
    """
    unmet: list[str] = []
    for skill_name, must_be_absent in LADDER_CLAIMS:
        before = stored.get(skill_name)
        if before is None or must_be_absent not in before:
            continue
        if must_be_absent in corrected[skill_name]:
            unmet.append(f"{skill_name} still teaches {must_be_absent!r}")
    return unmet


def upgrade() -> None:
    conn = op.get_bind()
    stored = {
        str(name): str(text)
        for name, text in conn.exec_driver_sql(
            "SELECT skill_name, source_text FROM managed_skills"
        ).fetchall()
    }
    corrected = corrected_sources(stored)
    for skill_name, text in corrected.items():
        if text != stored[skill_name]:
            conn.exec_driver_sql(
                "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
                (text, skill_name),
            )
    unmet = unmet_claims(stored, corrected)
    if unmet:
        # Inside the transaction, so the upgrade rolls back rather than reporting a
        # correction it did not make.
        raise RuntimeError("skill ladder corrections did not land: " + "; ".join(sorted(unmet)))


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
