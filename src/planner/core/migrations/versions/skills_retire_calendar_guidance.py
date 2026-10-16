"""Retire active calendar instructions without rewriting owner prose or versions.

Revision ID: skills_retire_calendar_guidance
Revises: retire_planning_workers
"""

from __future__ import annotations

from alembic import op

from planner.core.migrations.retirement_skills import corrected_sources, stale_calendar_instructions

revision = "skills_retire_calendar_guidance"
down_revision = "retire_planning_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    stored = {
        str(name): str(source)
        for name, source in conn.exec_driver_sql(
            "SELECT skill_name, source_text FROM managed_skills"
        ).fetchall()
    }
    corrected = corrected_sources(stored)
    unresolved = stale_calendar_instructions(corrected)
    if unresolved:
        raise RuntimeError("Unresolved active calendar skill instructions: " + "; ".join(unresolved))
    for name, source in corrected.items():
        if source != stored[name]:
            conn.exec_driver_sql(
                "UPDATE managed_skills SET source_text=? WHERE skill_name=?", (source, name)
            )


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
