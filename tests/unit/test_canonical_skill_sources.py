import concurrent.futures
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.environments.hermes_home import provision_planner_home_skills
from planner.managed_skills import managed_skills_home
from planner.skill_sources import (
    RETIRED_PANELS_SKILL_NAMES,
    provision_native_backend_skills,
)
from planner.worker_settings import service


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A database beside ``tmp_path``, so the managed skills home is ``tmp_path/skills``."""
    conn = connect(str(tmp_path / "planner.db"))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_supervisor_edit_reaches_every_backend_home_from_one_managed_source(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    managed = managed_skills_home(tmp_path)
    homes = {
        "hermes": tmp_path / "hermes-home",
        "codex": tmp_path / "codex-home",
        "claude": tmp_path / "claude-home",
    }
    provision_planner_home_skills(homes["hermes"], configured_database_parent=tmp_path)
    provision_native_backend_skills(homes["codex"], tmp_path)
    provision_native_backend_skills(homes["claude"], tmp_path)

    service.save_skill(
        database,
        "panels-sprint-item-supervisor",
        {
            "description": "Future conversations read this revision",
            "markdown_body": "# Supervisor\n\nUse canonical context.\n",
        },
        now=1,
        database_parent=tmp_path,
    )

    canonical = managed / "panels-sprint-item-supervisor"
    for home in homes.values():
        exposed = home / "skills" / "panels-sprint-item-supervisor"
        assert exposed.resolve() == canonical.resolve()
        assert "Future conversations read this revision" in (
            exposed / "SKILL.md"
        ).read_text(encoding="utf-8")


def test_native_skills_directory_preserves_custom_entries_and_replaces_panels_collision(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    del database
    managed = managed_skills_home(tmp_path)
    native_skills = tmp_path / "provider" / "skills"
    (native_skills / "custom").mkdir(parents=True)
    (native_skills / "custom" / "SKILL.md").write_text("custom", encoding="utf-8")
    (native_skills / "panels-worker-coding").mkdir()
    (native_skills / "panels-worker-coding" / "SKILL.md").write_text("stale", encoding="utf-8")
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        (native_skills / retired_skill_name).write_text("retired", encoding="utf-8")

    provision_native_backend_skills(tmp_path / "provider", tmp_path)
    provision_native_backend_skills(tmp_path / "provider", tmp_path)

    assert (native_skills / "custom" / "SKILL.md").read_text(encoding="utf-8") == "custom"
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        assert not (native_skills / retired_skill_name).exists()
    assert (native_skills / "panels-worker-coding").resolve() == (
        managed / "panels-worker-coding"
    ).resolve()


def test_skill_edit_rejects_an_unknown_skill_name(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    with pytest.raises(PlannerError):
        service.save_skill(database, "..", {"description": "bad"}, now=1, database_parent=tmp_path)


def test_concurrent_skill_field_edits_preserve_both_fields(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    current = next(
        skill for skill in service.read_skills_home(database).skills if skill.name == "panels"
    )
    database_path = str(tmp_path / "planner.db")

    def edit(payload: dict[str, object]) -> None:
        # Each thread needs its own connection: one connection cannot carry two writers.
        conn = connect(database_path)
        try:
            service.save_skill(conn, "panels", payload, now=1, database_parent=tmp_path)
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(edit, {"description": "parallel"}),
            executor.submit(edit, {"markdown_body": current.markdown_body + "\nparallel\n"}),
        ]
        [future.result() for future in futures]

    final = next(
        skill for skill in service.read_skills_home(database).skills if skill.name == "panels"
    )
    assert final.description == "parallel"
    assert final.markdown_body.endswith("parallel\n")
