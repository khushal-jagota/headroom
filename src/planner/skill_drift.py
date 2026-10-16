"""Which skill rows no longer say what the packaged seed they started from says.

The packaged tree seeds a database that holds no skills, and nothing consults it again. So
a row and its seed drift apart in silence, and an editor who reaches the wrong copy has
nothing to tell them. This reports the difference, in both directions: a row whose text no
longer matches its seed, a row the package does not ship, and a seed no row holds.

A row is the owner's text, so nothing here rewrites it. A difference is not a fault either:
an owner edit is what the product is for. The report exists so that the difference is
visible rather than silent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from planner.managed_skills import (
    PACKAGED_SKILLS_ROOT,
    SKILL_FILE_NAME,
    read_all_skill_sources,
)

ROW_DIFFERS_FROM_SEED: Final = "row_differs_from_seed"
ROW_WITHOUT_SEED: Final = "row_without_seed"
SEED_WITHOUT_ROW: Final = "seed_without_row"


@dataclass(frozen=True)
class SkillDrift:
    """One skill where the stored row and the packaged seed do not agree."""

    skill_name: str
    state: str

    def as_dict(self) -> dict[str, str]:
        return {"skill_name": self.skill_name, "state": self.state}


def packaged_skill_sources() -> dict[str, str]:
    """The text every packaged seed holds, by skill name."""
    found: dict[str, str] = {}
    for directory in sorted(PACKAGED_SKILLS_ROOT.iterdir(), key=lambda path: path.name):
        skill_file = directory / SKILL_FILE_NAME
        if not directory.is_dir() or not skill_file.is_file():
            continue
        found[directory.name] = skill_file.read_text(encoding="utf-8")
    return found


def skill_drift(conn: sqlite3.Connection) -> tuple[SkillDrift, ...]:
    """Every skill where the stored row and the packaged seed do not agree."""
    seeds = packaged_skill_sources()
    rows = read_all_skill_sources(conn)
    found: list[SkillDrift] = []
    for skill_name in sorted(set(seeds) | set(rows)):
        seed = seeds.get(skill_name)
        row = rows.get(skill_name)
        if seed is None:
            found.append(SkillDrift(skill_name, ROW_WITHOUT_SEED))
        elif row is None:
            found.append(SkillDrift(skill_name, SEED_WITHOUT_ROW))
        elif row != seed:
            found.append(SkillDrift(skill_name, ROW_DIFFERS_FROM_SEED))
    return tuple(found)
