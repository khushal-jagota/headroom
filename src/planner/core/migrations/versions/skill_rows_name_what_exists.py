"""Correct the skill rows that still name a command, Stage or skill this build removed.

Revision ID: skill_rows_name_what_exists
Revises: baseline_2026_09

A skill row is the owner's text. The packaged tree seeds a database that holds no
skills and is never consulted again, so two commits that corrected the packaged
skills could not reach a database that already had rows. The rename before them
tried, by replacing the passages the build ships. An edited row does not contain
those passages, the replacement found nothing, and nothing checked. That silence is
the whole bug.

This revision replaces text that is provably the build's own. Every span in
``SPAN_CORRECTIONS`` occurs verbatim in a version of that skill this repository
shipped, checked when the data was generated, so no correction can remove a sentence
the owner wrote. A moved command name is different: it is a literal a worker types
rather than prose, so it moves wherever it appears.

What this revision refuses to do is fail quietly. It declares in ``CLAIMS`` what must
be gone from which row, and raises if a claim it made did not land. It does not raise
for anything else a row still names. A stale row costs an agent one failed turn; a
refused upgrade costs a deploy at the point where there is no rolling the code back.
``panels skill check`` is where everything else surfaces.

``updated_at`` is deliberately left alone. These corrections are the build's, and
moving the timestamp would present them as the owner's own edit.
"""

from __future__ import annotations

from alembic import op

from planner.core.migrations.skill_row_correction_data import (
    CLAIMS,
    SPAN_CORRECTIONS,
    TOKEN_CORRECTIONS,
)

revision = "skill_rows_name_what_exists"
down_revision = "baseline_2026_09"
branch_labels = None
depends_on = None


def corrected_sources(stored: dict[str, str]) -> dict[str, str]:
    """Apply every correction to the rows as they stand. Exported so a test can read it."""
    corrected = dict(stored)
    for skill_name, replaced_text, replacement, _why in SPAN_CORRECTIONS:
        text = corrected.get(skill_name)
        if text is None or replaced_text not in text:
            continue
        corrected[skill_name] = text.replace(replaced_text, replacement)
    for old_token, new_token in TOKEN_CORRECTIONS:
        for skill_name, text in corrected.items():
            if old_token in text:
                corrected[skill_name] = text.replace(old_token, new_token)
    return corrected


def unmet_claims(stored: dict[str, str], corrected: dict[str, str]) -> list[str]:
    """Claims this revision made against rows that held the text, and did not keep.

    A row that never held the text was never claimed, so a fresh database — where
    seeding runs after the migrations and this table is still empty — reports nothing.
    """
    unmet: list[str] = []
    for skill_name, must_be_absent in CLAIMS:
        before = stored.get(skill_name)
        if before is None or must_be_absent not in before:
            continue
        if must_be_absent in corrected[skill_name]:
            unmet.append(f"{skill_name} still names {must_be_absent!r}")
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
        raise RuntimeError("skill row corrections did not land: " + "; ".join(sorted(unmet)))


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
