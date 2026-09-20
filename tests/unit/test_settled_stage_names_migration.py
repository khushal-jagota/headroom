"""The settled Stage names reach the rows, and nothing else moves.

The rename is a label edit. The cases here hold the two halves of that claim apart: every
name a reader sees becomes the settled one, and every id, every withdrawn name, and every
label the owner already changed stays exactly as it was.
"""

from __future__ import annotations

import copy
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from planner.core.db import connect, create_schema
from planner.core.migrations.versions.settled_stage_names import RENAMES, _rewrite_labels
from planner.core.migrations.versions.worker_types_in_database import SHIPPED_WORKER_TYPES
from planner.worker_types.store import read_definition, read_definitions

# What a reader must see afterwards, by Worker type and field id.
SETTLED = {
    ("coding", "success"): "Success Condition",
    ("coding", "approach"): "What Changes",
    ("general", "execution"): "Work Done",
    ("debugging", "structural_diagnosis"): "Root Cause",
    ("debugging", "solution"): "Proposed Fix",
    ("initiative_planning", "rough_shape"): "Rough Split into Parts",
    ("initiative_planning", "ticket_outlines"): "Proposed Tickets",
    ("new_worker", "understanding"): "Purpose and Boundaries",
    ("new_worker", "thinking"): "What Good Looks Like at Each Stage",
    ("new_worker", "runtime_defaults"): "Model and Effort",
    ("product_design", "direction"): "Best Guess and Open Options",
    ("planning-day", "direction"): "Today's Direction",
    ("planning-midday-check", "action"): "Agreed Intervention",
    ("exploration", "research"): "Findings",
    ("research", "research"): "Findings",
    ("exploration", "follow_up"): "Proposed Follow-up",
}

# One word, two meanings. These were proposed and withdrawn, so they keep the shipped name
# even though a Worker type beside them renames the same field id.
KEPT = {
    ("exploration", "understanding"): "Understanding",
    ("personal", "outcome"): "Outcome",
    ("exploration", "research_plan"): "Research Plan",
    ("research", "research_plan"): "Research Plan",
    ("initiative_planning", "question_tree"): "Question Tree",
    ("initiative_planning", "question_answers"): "Question Answers",
    ("coding", "implementation"): "Implementation",
    ("coding", "plan"): "Plan",
    ("product_design", "wireframe"): "Wireframe",
    ("debugging", "problem_understanding"): "Problem Understanding",
}


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(str(tmp_path / "planner.db"))
    create_schema(connection)
    yield connection
    connection.close()


def _shipped(worker_type: str) -> dict[str, Any]:
    for definition in SHIPPED_WORKER_TYPES:
        if definition["worker_type"] == worker_type:
            return copy.deepcopy(definition)
    raise AssertionError(f"no shipped definition for {worker_type}")


def test_every_settled_name_reaches_its_field_and_its_stage(conn: sqlite3.Connection) -> None:
    for (worker_type, field_id), label in SETTLED.items():
        definition = read_definition(conn, worker_type)
        assert definition.field_definition(field_id).label == label
        stage = definition.stage_definition(definition.stage_gated_by(field_id))
        assert stage.label == label, (worker_type, field_id)


def test_brief_and_consequences_reach_every_type_that_has_them(
    conn: sqlite3.Connection,
) -> None:
    briefed = 0
    for definition in read_definitions(conn):
        assert definition.field_definition("closeout").label == "Consequences"
        if definition.has_field("kickoff"):
            assert definition.field_definition("kickoff").label == "Brief"
            briefed += 1
    assert briefed == 13


def test_the_withdrawn_and_unchanged_names_stay(conn: sqlite3.Connection) -> None:
    for (worker_type, field_id), label in KEPT.items():
        definition = read_definition(conn, worker_type)
        assert definition.field_definition(field_id).label == label


def test_no_id_moves(conn: sqlite3.Connection) -> None:
    """The whole rename is a label edit. Every id is what it was."""
    for definition in read_definitions(conn):
        shipped = _shipped(definition.worker_type)
        assert [field.id for field in definition.fields] == [
            field["id"] for field in shipped["fields"]
        ]
        assert [stage.id for stage in definition.stages] == [
            stage["id"] for stage in shipped["stages"]
        ]
        assert [stage.gating_field for stage in definition.stages] == [
            stage["gating_field"] for stage in shipped["stages"]
        ]


def test_the_research_worker_type_keeps_its_own_name(conn: sqlite3.Connection) -> None:
    """Only the Stage becomes Findings. The type is still called Research."""
    definition = read_definition(conn, "research")
    assert definition.label == "Research"
    assert definition.field_definition("research").label == "Findings"


def test_a_label_the_owner_changed_is_left_alone() -> None:
    definition = _shipped("debugging")
    for field in definition["fields"]:
        if field["id"] == "solution":
            field["label"] = "A Name The Owner Picked"
    for stage in definition["stages"]:
        if stage["gating_field"] == "solution":
            stage["label"] = "A Name The Owner Picked"

    _rewrite_labels(definition, forward=True)

    solution = [field for field in definition["fields"] if field["id"] == "solution"]
    assert solution[0]["label"] == "A Name The Owner Picked"
    closeout = [field for field in definition["fields"] if field["id"] == "closeout"]
    assert closeout[0]["label"] == "Consequences"


def test_rewriting_twice_changes_nothing_the_second_time() -> None:
    definition = _shipped("coding")
    first = _rewrite_labels(definition, forward=True)
    # Brief, Success Condition, What Changes and Consequences, each on its field and
    # on the Stage it gates. Plan and Implementation keep their names.
    assert first == 8
    assert _rewrite_labels(definition, forward=True) == 0


def test_the_downgrade_puts_the_shipped_names_back() -> None:
    for shipped in SHIPPED_WORKER_TYPES:
        definition = copy.deepcopy(shipped)
        _rewrite_labels(definition, forward=True)
        _rewrite_labels(definition, forward=False)
        assert definition == shipped, shipped["worker_type"]


def test_every_rename_names_a_field_that_exists() -> None:
    """A rename aimed at a field no Worker type declares would silently do nothing."""
    for field_id, shipped_label, _settled, types in RENAMES:
        matched = 0
        for definition in SHIPPED_WORKER_TYPES:
            if types is not None and definition["worker_type"] not in types:
                continue
            for field in definition["fields"]:
                if field["id"] == field_id:
                    assert field["label"] == shipped_label, definition["worker_type"]
                    matched += 1
        assert matched > 0, field_id
        if types is not None:
            assert matched == len(types), field_id
