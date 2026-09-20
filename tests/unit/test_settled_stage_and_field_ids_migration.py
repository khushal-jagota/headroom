"""What the rename carries across: the ids move, and nothing is left standing on one.

The cases are the ones a real database presents at this revision: a Ticket mid-flight
with a parked proposal, two Worker types whose middle Stages are different, a Stage that
is not named after the field it gates, a skill that spells the ids three different ways,
and a Ticket whose stored keys would collide if the rename were applied blindly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema
from planner.core.migrations.versions.settled_stage_and_field_ids import moved_skill_text
from planner.worker_types.store import read_definition

PREVIOUS_REVISION = "one_notification_path"

# A coding Ticket part-way through, with a settled value, a blank, and a parked proposal.
# Three of these four keys move and `plan` does not, so the count is the same either way
# and a collision would be the only thing that could change it.
_CODING_FIELD_VALUES = {
    "kickoff": "Why this is worth doing",
    "success": "It works end to end",
    "approach": "",
    "plan": "The plan",
}
_CODING_PROPOSAL = {
    "field": "approach",
    "body": "A draft nobody has approved",
    "proposed_by": "worker",
    "created_at": 5,
}

# The probe Worker type names its Closeout Stage `needs_landing` rather than
# `needs_closeout`, and the rule allows that. A stored Worker type may do the same, so
# the general type is put in that shape here and has to come out of the rename with its
# own Stage id intact and only the field it gates moved.
_GENERAL_LANDING_STAGE = "needs_landing"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def _seed_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    worker_type: str,
    stage: str,
    ceiling: str,
    field_values: dict[str, str],
    pending_proposal: dict[str, object] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO tickets "
        "(id, worker_type, employee_backend, stage, title, ceiling, field_values, "
        "pending_proposal, created_at, updated_at) VALUES (?, ?, 'codex', ?, ?, ?, ?, ?, 1, 1)",
        (
            ticket_id,
            worker_type,
            stage,
            f"Title {ticket_id}",
            ceiling,
            json.dumps(field_values),
            None if pending_proposal is None else json.dumps(pending_proposal),
        ),
    )


def _rename_general_closeout_stage(conn: sqlite3.Connection) -> None:
    """Give general's Closeout Stage a name of its own, the way the probe does."""
    stored = json.loads(
        str(
            conn.execute(
                "SELECT definition_json FROM worker_types WHERE worker_type = 'general'"
            ).fetchone()["definition_json"]
        )
    )
    for stage in stored["stages"]:
        if stage["id"] == "needs_closeout":
            stage["id"] = _GENERAL_LANDING_STAGE
    conn.execute(
        "UPDATE worker_types SET definition_json = ? WHERE worker_type = 'general'",
        (json.dumps(stored),),
    )


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    db_path = tmp_path / "settled-ids.db"
    conn = _upgrade_to_previous_revision(db_path)
    _rename_general_closeout_stage(conn)
    _seed_ticket(
        conn,
        "t_coding",
        worker_type="coding",
        stage="needs_approach",
        ceiling="needs_closeout",
        field_values=_CODING_FIELD_VALUES,
        pending_proposal=_CODING_PROPOSAL,
    )
    _seed_ticket(
        conn,
        "t_general",
        worker_type="general",
        stage="needs_execution",
        ceiling=_GENERAL_LANDING_STAGE,
        field_values={"kickoff": "Why", "execution": "What happened"},
    )
    # The two Worker types are standing on Stages that spell differently, so one shared
    # answer cannot satisfy both rows.
    conn.execute(
        "INSERT INTO ticket_paired_stage_openers (ticket_id, stage, opened_at) "
        "VALUES ('t_coding', 'needs_success', 1), ('t_general', 'needs_execution', 1)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_a_ticket_moves_its_stage_ceiling_values_and_parked_proposal_together(
    seeded: Path,
) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        row = conn.execute(
            "SELECT stage, ceiling, field_values, pending_proposal FROM tickets "
            "WHERE id = 't_coding'"
        ).fetchone()
        assert str(row["stage"]) == "needs_what_changes"
        assert str(row["ceiling"]) == "needs_consequences"
        stored = json.loads(str(row["field_values"]))
        assert stored == {
            "brief": "Why this is worth doing",
            "success_condition": "It works end to end",
            "what_changes": "",
            "plan": "The plan",
        }
        # A key the rename dropped or merged would read as text gone dark, so the count
        # is the thing to hold onto and not just the contents.
        assert len(stored) == len(_CODING_FIELD_VALUES)
        assert json.loads(str(row["pending_proposal"])) == {
            **_CODING_PROPOSAL,
            "field": "what_changes",
        }
    finally:
        conn.close()


def test_a_paired_stage_opener_moves_and_moves_per_worker_type(seeded: Path) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        openers = {
            str(row["ticket_id"]): str(row["stage"])
            for row in conn.execute("SELECT ticket_id, stage FROM ticket_paired_stage_openers")
        }
        assert openers == {
            "t_coding": "needs_success_condition",
            "t_general": "needs_work_done",
        }
        # The mapping is per Worker type, so the two rows cannot land on one answer.
        assert openers["t_coding"] != openers["t_general"]
    finally:
        conn.close()


def test_a_stage_not_named_after_its_field_keeps_its_own_id(seeded: Path) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        general = read_definition(conn, "general")
        stage_ids = list(general.stage_ids())
        assert _GENERAL_LANDING_STAGE in stage_ids
        assert "needs_closeout" not in stage_ids
        assert "needs_consequences" not in stage_ids
        # The Stage kept its name and the field it gates moved anyway.
        assert general.gating_field(_GENERAL_LANDING_STAGE) == "consequences"
        assert general.has_field("consequences")
        assert not general.has_field("closeout")
        # The Stage that is spelled `needs_<field id>` moved in the same pass.
        assert "needs_work_done" in stage_ids
    finally:
        conn.close()


def test_moved_skill_text_moves_stage_ids_anywhere_and_field_ids_only_in_backticks() -> None:
    definition = {"worker_type": "coding"}
    source = (
        "## needs_success\n"
        "\n"
        r"**needs\_success** is where a worker settles it." "\n"
        "\n"
        "Send `success` when the check passes.\n"
        "\n"
        "The **success** of the work is a word, not an id.\n"
    )

    moved = moved_skill_text(source, definition)

    # A Stage id is unambiguous, so it moves in a heading and in escaped bold alike.
    assert "## needs_success_condition\n" in moved
    assert r"**needs\_success\_condition**" in moved
    # A field id moves only where it can only be a literal.
    assert "Send `success_condition` when" in moved
    # The lowercase bold word is the label, which this change does not own.
    assert "The **success** of the work" in moved
    assert "`success`" not in moved
    # Running it again over already-moved text changes nothing.
    assert moved_skill_text(moved, definition) == moved


def test_a_rename_that_would_collapse_two_keys_rolls_the_whole_upgrade_back(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "collision.db"
    conn = _upgrade_to_previous_revision(db_path)
    # Both keys rename onto `brief`, so one of them would simply stop existing.
    colliding = {"kickoff": "The old spelling", "brief": "The new spelling"}
    _seed_ticket(
        conn,
        "t_collides",
        worker_type="coding",
        stage="needs_kickoff",
        ceiling="needs_kickoff",
        field_values=colliding,
    )
    conn.commit()
    conn.close()

    conn = connect(str(db_path))
    try:
        with pytest.raises(RuntimeError, match="renaming field keys changed the stored text"):
            create_schema(conn)
    finally:
        conn.close()

    # The count runs inside the transaction, so the refusal leaves the database exactly
    # where it was rather than part-way through the rename.
    after = connect(str(db_path))
    try:
        assert (
            str(after.execute("SELECT version_num FROM alembic_version").fetchone()[0])
            == PREVIOUS_REVISION
        )
        row = after.execute(
            "SELECT stage, field_values FROM tickets WHERE id = 't_collides'"
        ).fetchone()
        assert str(row["stage"]) == "needs_kickoff"
        assert json.loads(str(row["field_values"])) == colliding
        stored = json.loads(
            str(
                after.execute(
                    "SELECT definition_json FROM worker_types WHERE worker_type = 'coding'"
                ).fetchone()["definition_json"]
            )
        )
        assert [stage["id"] for stage in stored["stages"]][0] == "needs_kickoff"
    finally:
        after.close()
