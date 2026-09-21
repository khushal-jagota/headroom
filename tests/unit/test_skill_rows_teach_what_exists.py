"""The stage ladders a deploy corrects, and the prose it deliberately leaves alone."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import click
import pytest

from planner.core.db import connect, create_schema
from planner.core.migrations.skill_ladder_correction_data import (
    LADDER_CLAIMS,
    LADDER_CORRECTIONS,
)
from planner.core.migrations.versions.skill_rows_teach_what_exists import (
    corrected_sources,
    unmet_claims,
)
from planner.skill_staleness import (
    declared_stage_names,
    stale_references,
    stale_references_in,
    terminal_stage_names,
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


def test_a_ladder_of_names_this_build_has_is_not_reported() -> None:
    text = (
        "The visible sequence is **Brief → Success Condition → What Changes → Plan "
        "→ Implementation → Consequences → Done**."
    )

    assert _ladder_findings(text) == []


def test_a_ladder_that_survived_a_rename_names_every_stage_the_build_lost() -> None:
    text = (
        "Ticket workers shape a Ticket through **Kickoff → Success → Approach → Plan\n"
        "→ Implementation → Closeout → Done**, filling one field at each step."
    )

    assert sorted(_ladder_findings(text)) == ["Approach", "Closeout", "Kickoff", "Success"]


def test_an_arrow_that_is_not_a_ladder_is_left_alone() -> None:
    """The Simplified Technical English rule in ``panels-worker`` is bold, and has arrows.

    It does not end at a terminal Stage, which is the whole difference between a taught
    sequence and any other arrow in the text.
    """
    text = '**No present perfect ("has completed" → "completed"). No "-ing" forms.**'

    assert _ladder_findings(text) == []


def test_a_stage_label_in_ordinary_prose_is_left_alone() -> None:
    """Deliberate. A label is an ordinary phrase, so prose cannot be searched for one.

    ``kickoff`` names the live ``--kickoff-note`` option and the sprint kickoff document,
    and ``approach`` is an English word. A check that reports correct text gets read as
    noise, and a noisy check gets read like silence.
    """
    text = (
        "Use the outlined Worker type, title, placement, and kickoff context.\n"
        "A good plan goes through how we will execute an approach.\n"
        "Closeout is the only canonical-write phase."
    )

    assert _ladder_findings(text) == []


def test_a_sequence_introduced_inside_the_bold_still_reads_as_a_ladder() -> None:
    text = "**Stages: Brief → Kickoff → Consequences → Done.**"

    assert _ladder_findings(text) == ["Kickoff"]


def test_a_correction_renames_the_stage_and_keeps_the_sentence_around_it() -> None:
    skill_name, replaced_text, _replacement, _why = LADDER_CORRECTIONS[0]
    owner_sentence = "I wrote this paragraph myself and it must survive.\n"
    stored = {skill_name: owner_sentence + replaced_text + "\nmy closing line\n"}

    corrected = corrected_sources(stored)

    assert owner_sentence in corrected[skill_name]
    assert "my closing line" in corrected[skill_name]
    assert replaced_text not in corrected[skill_name]


def test_a_correction_that_does_not_land_is_reported_rather_than_passing() -> None:
    skill_name, must_be_absent = LADDER_CLAIMS[0]
    stored = {skill_name: f"an edited row that still teaches {must_be_absent} somewhere"}

    unmet = unmet_claims(stored, corrected_sources(stored))

    assert unmet == [f"{skill_name} still teaches {must_be_absent!r}"]


def test_a_row_that_never_held_the_text_is_never_claimed() -> None:
    stored: dict[str, str] = {}

    assert unmet_claims(stored, corrected_sources(stored)) == []


def test_the_ladder_check_reads_the_stage_names_this_build_declares(
    database: sqlite3.Connection,
) -> None:
    """No list of yesterday's names: both sets come from the Worker types in the database."""
    names = declared_stage_names(database)
    terminal = terminal_stage_names(database)

    assert {"Success Condition", "needs_success_condition", "Consequences"} <= names
    assert "Kickoff" not in names
    assert terminal <= names
    assert "Success Condition" not in terminal


def test_a_database_this_build_made_teaches_only_ladders_this_build_has(
    database: sqlite3.Connection,
) -> None:
    assert stale_references(database) == ()
