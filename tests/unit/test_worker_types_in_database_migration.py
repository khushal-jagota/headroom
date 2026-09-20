"""What the migration carries across: the Worker types, their skills, and the Chief.

The overlay it reads from — ``data/worker-settings/`` — is what the owner had been
editing, so the cases here are the ones a real folder presents: an edited block, a block
with keys that stopped meaning anything, a directory that is not a Worker type at all,
and no folder whatsoever.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema
from planner.core.migrations.versions.worker_types_in_database import SHIPPED_WORKER_TYPES
from planner.managed_skills import read_all_skill_sources, read_skill_source
from planner.skill_sources import panels_skill_root
from planner.worker_settings import service
from planner.worker_types.store import read_definition, read_definitions


def _write_overlay(parent: Path, key: str, payload: dict[str, object]) -> None:
    path = parent / "worker-settings" / key / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _launch(backend: str, model: str, effort: str | None) -> dict[str, object]:
    return {
        "employee_backend": backend,
        "employee_launch_model": model,
        "employee_launch_reasoning_effort": effort,
    }


@pytest.fixture
def parent(tmp_path: Path) -> Path:
    return tmp_path


def _open(parent: Path) -> sqlite3.Connection:
    conn = connect(str(parent / "planner.db"))
    create_schema(conn)
    return conn


def test_every_shipped_type_arrives_with_its_stages_fields_and_profile(parent: Path) -> None:
    conn = _open(parent)
    try:
        stored = {definition.worker_type: definition for definition in read_definitions(conn)}
        assert len(stored) == len(SHIPPED_WORKER_TYPES)
        for shipped in SHIPPED_WORKER_TYPES:
            definition = stored[str(shipped["worker_type"])]
            assert definition.label == shipped["label"]
            assert [stage.id for stage in definition.stages] == [
                stage["id"] for stage in shipped["stages"]
            ]
            assert [field.id for field in definition.fields] == [
                field["id"] for field in shipped["fields"]
            ]
            profile: dict[str, object] = shipped["profile"]
            assert definition.worker_profile.specialist_skill == profile["specialist_skill"]
    finally:
        conn.close()


def test_every_frozen_definition_declares_a_closeout(parent: Path) -> None:
    """The migration carries its own frozen copies and never calls the write guard.

    So the guard cannot vouch for them. This does, and it keeps doing so if anyone
    reaches back into the frozen list.
    """
    for shipped in SHIPPED_WORKER_TYPES:
        fields = [str(field["id"]) for field in shipped["fields"]]
        gating = [stage["gating_field"] for stage in shipped["stages"]]
        assert "closeout" in fields, shipped["worker_type"]
        assert gating.count("closeout") == 1, shipped["worker_type"]


def test_a_database_with_no_overlay_takes_the_shipped_launch_defaults(parent: Path) -> None:
    conn = _open(parent)
    try:
        coding = read_definition(conn, "coding")
        assert coding.worker_profile.default_backend == "codex"
        assert coding.worker_profile.default_model == "gpt-5.6-sol"
        assert service.read_chief_settings(conn).launch_defaults.employee_backend == "codex"
    finally:
        conn.close()


def test_an_edited_overlay_block_is_what_the_type_launches_on(parent: Path) -> None:
    _write_overlay(
        parent,
        "coding",
        {"worker_type": "coding", "launch_defaults": _launch("claude", "claude-opus-5", None)},
    )

    conn = _open(parent)
    try:
        profile = read_definition(conn, "coding").worker_profile
        assert profile.default_backend == "claude"
        assert profile.default_model == "claude-opus-5"
        assert profile.default_reasoning_effort is None
    finally:
        conn.close()


def test_overlay_keys_that_stopped_meaning_anything_are_left_behind(parent: Path) -> None:
    """An older overlay carried ceiling and ownership policy. Only the launch block moves."""
    _write_overlay(
        parent,
        "coding",
        {
            "worker_type": "coding",
            "suggested_next_ceiling": "done",
            "stage_ownership_defaults": {"needs_success": "user"},
            "launch_defaults": _launch("claude", "claude-opus-5", "high"),
        },
    )

    conn = _open(parent)
    try:
        record = json.loads(
            str(
                conn.execute(
                    "SELECT definition_json FROM worker_types WHERE worker_type = 'coding'"
                ).fetchone()["definition_json"]
            )
        )
        assert set(record) == {
            "worker_type",
            "label",
            "stages",
            "fields",
            "profile",
        }
        assert record["profile"]["default_reasoning_effort"] == "high"
    finally:
        conn.close()


def test_an_overlay_block_that_names_no_model_is_not_carried_over(parent: Path) -> None:
    _write_overlay(
        parent,
        "coding",
        {"worker_type": "coding", "launch_defaults": _launch("claude", "", "high")},
    )

    conn = _open(parent)
    try:
        profile = read_definition(conn, "coding").worker_profile
        assert profile.default_backend == "codex"
        assert profile.default_model == "gpt-5.6-sol"
    finally:
        conn.close()


def test_an_overlay_directory_that_is_not_a_worker_type_becomes_nothing(parent: Path) -> None:
    _write_overlay(
        parent,
        "initiative_review_left_over",
        {
            "worker_type": "initiative_review_left_over",
            "launch_defaults": _launch("codex", "m", None),
        },
    )

    conn = _open(parent)
    try:
        assert "initiative_review_left_over" not in [
            definition.worker_type for definition in read_definitions(conn)
        ]
    finally:
        conn.close()


def test_the_chief_keeps_the_launch_defaults_it_was_edited_to(parent: Path) -> None:
    _write_overlay(
        parent,
        "chief_of_staff",
        {
            "employee_id": "chief_of_staff",
            "label": "Chief of Staff",
            "launch_defaults": _launch("hermes", "openai-codex:gpt-5.6-sol", None),
        },
    )

    conn = _open(parent)
    try:
        chief = service.read_chief_settings(conn)
        assert chief.label == "Chief of Staff"
        assert chief.launch_defaults.employee_backend == "hermes"
        assert chief.launch_defaults.employee_launch_model == "openai-codex:gpt-5.6-sol"
    finally:
        conn.close()


def test_a_skill_the_owner_edited_is_preferred_over_the_packaged_one(parent: Path) -> None:
    edited = parent / "skills" / "panels-worker-coding" / "SKILL.md"
    edited.parent.mkdir(parents=True)
    edited.write_text(
        '---\nname: "panels-worker-coding"\ndescription: "Edited in the product"\n---\n\nMine.\n',
        encoding="utf-8",
    )

    conn = _open(parent)
    try:
        assert "Edited in the product" in read_skill_source(conn, "panels-worker-coding")
        # A skill the owner never touched still arrives, from what shipped.
        packaged = (panels_skill_root() / "panels" / "SKILL.md").read_text(encoding="utf-8")
        assert read_skill_source(conn, "panels") == packaged
    finally:
        conn.close()


def test_the_two_skills_this_change_rewrote_arrive_from_the_package(parent: Path) -> None:
    """They teach how to declare a Worker type, so the edited copy is the stale one."""
    for skill_name in ("panels-worker-new-worker", "panels-worker-amend-worker"):
        edited = parent / "skills" / skill_name / "SKILL.md"
        edited.parent.mkdir(parents=True)
        edited.write_text(
            f'---\nname: "{skill_name}"\ndescription: "Edit the Python file"\n---\n\nOld way.\n',
            encoding="utf-8",
        )

    conn = _open(parent)
    try:
        for skill_name in ("panels-worker-new-worker", "panels-worker-amend-worker"):
            packaged = (panels_skill_root() / skill_name / "SKILL.md").read_text(encoding="utf-8")
            assert read_skill_source(conn, skill_name) == packaged
    finally:
        conn.close()


def test_every_packaged_skill_arrives(parent: Path) -> None:
    conn = _open(parent)
    try:
        stored = set(read_all_skill_sources(conn))
        packaged = {
            directory.name
            for directory in panels_skill_root().iterdir()
            if directory.is_dir() and (directory / "SKILL.md").is_file()
        }
        assert packaged <= stored
    finally:
        conn.close()


def test_opening_the_same_database_again_changes_nothing(parent: Path) -> None:
    first = _open(parent)
    try:
        before = [
            (str(row["worker_type"]), str(row["definition_json"]))
            for row in first.execute(
                "SELECT worker_type, definition_json FROM worker_types ORDER BY position"
            )
        ]
    finally:
        first.close()

    second = _open(parent)
    try:
        after = [
            (str(row["worker_type"]), str(row["definition_json"]))
            for row in second.execute(
                "SELECT worker_type, definition_json FROM worker_types ORDER BY position"
            )
        ]
    finally:
        second.close()

    assert before == after
