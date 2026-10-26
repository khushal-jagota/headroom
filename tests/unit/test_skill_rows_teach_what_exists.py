"""The stage ladders a deploy corrects, and the prose it deliberately leaves alone."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import click
import pytest

from planner.core.db import connect, create_schema
from planner.core.migrations.skill_ladder_correction_data import (
    LADDER_CORRECTIONS,
)
from planner.core.migrations.versions.skill_rows_teach_what_exists import (
    corrected_sources,
)
from planner.skill_staleness import (
    stale_references_in,
)

CODING_STAGES = frozenset(
    {
        "Brief",
        "Success Condition",
        "What Changes",
        "Plan",
        "Implementation",
        "Consequences",
        "Done",
    }
)
TERMINAL = frozenset({"Done", "done"})


def _ladder_findings(text: str, *, stage_names: frozenset[str] = CODING_STAGES) -> list[str]:
    found = stale_references_in(
        "some-skill",
        text,
        command_root=click.Group("panels"),
        stage_ids=frozenset(),
        stage_names=stage_names,
        terminal_names=TERMINAL,
    )
    return [item.reference for item in found if item.kind == "ladder"]


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(str(tmp_path / "planner.db"))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()












def test_a_correction_renames_the_stage_and_keeps_the_sentence_around_it() -> None:
    skill_name, replaced_text, _replacement, _why = LADDER_CORRECTIONS[0]
    owner_sentence = "I wrote this paragraph myself and it must survive.\n"
    stored = {skill_name: owner_sentence + replaced_text + "\nmy closing line\n"}

    corrected = corrected_sources(stored)

    assert owner_sentence in corrected[skill_name]
    assert "my closing line" in corrected[skill_name]
    assert replaced_text not in corrected[skill_name]
