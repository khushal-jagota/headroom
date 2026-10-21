import multiprocessing
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.support.skill_publication import publication_process

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.environments.hermes_home import provision_planner_home_skills
from planner.managed_skills import (
    atomic_replace_text,
    managed_skills_home,
    read_skill_source,
    write_skill_home,
    write_skill_source,
)
from planner.skill_sources import (
    RETIRED_PANELS_SKILL_NAMES,
    provision_native_backend_skills,
)
from planner.skill_versions import reconcile_managed_skill_versions
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
        "panels-manager",
        {
            "description": "Future conversations read this revision",
            "markdown_body": "# Supervisor\n\nUse canonical context.\n",
        },
        now=1,
        database_parent=tmp_path,
    )

    canonical = managed / "panels-manager"
    for home in homes.values():
        exposed = home / "skills" / "panels-manager"
        assert exposed.resolve() == canonical.resolve()
        assert "Future conversations read this revision" in (exposed / "SKILL.md").read_text(
            encoding="utf-8"
        )


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


@pytest.mark.parametrize("second_field", ["description", "markdown_body"])
def test_process_edit_publication_preserves_order_and_latest_fields(
    tmp_path: Path, database: sqlite3.Connection, second_field: str
) -> None:
    initial = service.read_skill(database, "panels")
    second: dict[str, object] = {second_field: "second revision"}
    _run_publication_race(tmp_path, "edit", second)
    final = service.read_skill(database, "panels")
    assert final.description == ("second revision" if second_field == "description" else "first")
    assert final.markdown_body == (
        "\nsecond revision\n" if second_field == "markdown_body" else initial.markdown_body
    )
    assert (tmp_path / "skills/panels/SKILL.md").read_text() == final.source_text
    versions = {
        bytes(row[0]).decode()
        for row in database.execute(
            "SELECT content FROM managed_skill_versions WHERE skill_name = 'panels'"
        )
    }
    assert final.source_text in versions
    assert any('description: "first"' in source for source in versions)


def test_startup_cannot_publish_an_old_snapshot_after_a_process_edit(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    _run_publication_race(tmp_path, "startup", {"description": "newer than startup"})
    final = read_skill_source(database, "panels")
    assert 'description: "newer than startup"' in final
    assert (tmp_path / "skills/panels/SKILL.md").read_text() == final


def _run_publication_race(
    tmp_path: Path, first_operation: str, second_patch: dict[str, object]
) -> None:
    context = multiprocessing.get_context("spawn")
    first_attempted, first_acquired, paused, release = [context.Event() for _ in range(4)]
    second_attempted, second_acquired, unused_pause, unused_release = [
        context.Event() for _ in range(4)
    ]
    database_path = str(tmp_path / "planner.db")
    first = context.Process(
        target=publication_process,
        args=(
            database_path,
            first_operation,
            {"description": "first"},
            first_attempted,
            first_acquired,
            paused,
            release,
            True,
        ),
    )
    second = context.Process(
        target=publication_process,
        args=(
            database_path,
            "edit",
            second_patch,
            second_attempted,
            second_acquired,
            unused_pause,
            unused_release,
            False,
        ),
    )
    first.start()
    try:
        assert paused.wait(15), "first operation never reached its publication pause"
        second.start()
        assert second_attempted.wait(15), "second process never attempted the home lock"
        assert not second_acquired.wait(0.15), "second process bypassed the home lock"
        release.set()
        first.join(15)
        second.join(15)
        assert first.exitcode == 0
        assert second.exitcode == 0
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(5)


def test_skill_patches_preserve_metadata_and_invalid_edits_do_not_change_state(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    source = "---\nname: panels\ndescription: initial\nmetadata:\n  owner: someone\n---\n\n# Body\n"
    write_skill_source(database, "panels", source, now=0)
    write_skill_home(database, tmp_path)
    edited = service.save_skill(
        database,
        "panels",
        {"name": "panels", "body": "# Replacement"},
        now=1,
        database_parent=tmp_path,
    )
    assert edited.description == "initial"
    assert "metadata:\n  owner: someone\n" in edited.source_text
    assert edited.markdown_body == "\n# Replacement\n"
    bad_edits: list[dict[str, object]] = [
        {},
        {"name": "panels"},
        {"name": "renamed", "description": "changed"},
        {"body": "one", "markdown_body": "two"},
        {"description": None},
        {"description": "   "},
        {"markdown_body": "  \n"},
        {"markdown_body": 7},
        {"unknown": "value"},
    ]
    for payload in bad_edits:
        with pytest.raises(PlannerError) as error:
            service.save_skill(database, "panels", payload, now=2, database_parent=tmp_path)
        assert error.value.code == ErrorCode.validation
        assert "field" in error.value.detail
        assert read_skill_source(database, "panels") == edited.source_text
        assert (tmp_path / "skills/panels/SKILL.md").read_text() == edited.source_text
    with pytest.raises(PlannerError) as missing:
        service.save_skill(
            database, "missing", {"description": "x"}, now=2, database_parent=tmp_path
        )
    assert missing.value.code == ErrorCode.not_found


@pytest.mark.parametrize("name", [".", "..", "../escaped", "/absolute", "a/b", "a\\b", "a\0b"])
def test_embedded_creation_refuses_unsafe_names_before_filesystem_access(
    tmp_path: Path, database: sqlite3.Connection, name: str
) -> None:
    with pytest.raises(PlannerError) as error:
        service.save_worker_type_skill(
            database,
            name,
            {"description": "initial", "markdown_body": "body"},
            now=1,
            database_parent=tmp_path / "not-created",
        )
    assert error.value.code == ErrorCode.validation
    assert not (tmp_path / "not-created").exists()


def test_skill_entrypoints_refuse_an_active_transaction_before_lock(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    database.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="no active transaction"):
            service.save_skill(
                database, "panels", {"description": "x"}, now=1, database_parent=tmp_path
            )
        with pytest.raises(RuntimeError, match="no active transaction"):
            write_skill_home(database, tmp_path)
        assert database.in_transaction
    finally:
        database.rollback()


def test_owner_edit_stands_in_the_row_and_the_file_when_version_history_fails(
    tmp_path: Path, database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is the authority, and it commits before the history is recorded.

    So a failure recording the version history cannot take the owner's edit back: the
    edit stands in the row, and in the copy agents read.
    """
    conn = database
    path = managed_skills_home(tmp_path) / "panels" / "SKILL.md"
    before = path.read_bytes()

    def fail_capture(*args: object, **kwargs: object) -> str:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "capture_skill_version", fail_capture)
    with pytest.raises(PlannerError) as error:
        service.save_skill(
            conn,
            "panels",
            {"description": "changed"},
            now=1,
            database_parent=tmp_path,
        )
    assert error.value.code == ErrorCode.skill_save_incomplete
    assert error.value.detail == {
        "skill_name": "panels",
        "saved": True,
        "published": True,
        "phase": "history",
    }
    assert "Read the canonical skill before retry" in error.value.message
    edited = read_skill_source(conn, "panels")
    assert edited != before.decode("utf-8")
    assert 'description: "changed"' in edited
    assert path.read_bytes() == edited.encode("utf-8")
    reconcile_managed_skill_versions(conn)
    assert (
        conn.execute(
            "SELECT content FROM managed_skill_versions WHERE skill_name = ? AND content = ?",
            ("panels", edited.encode("utf-8")),
        ).fetchone()
        is not None
    )


def test_file_failure_reports_saved_state_and_startup_repairs_the_copy(
    tmp_path: Path, database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = database
    path = managed_skills_home(tmp_path) / "panels/SKILL.md"
    old_bytes = path.read_bytes()
    original_replace = atomic_replace_text

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(service, "atomic_replace_text", fail_replace)
    with pytest.raises(PlannerError) as error:
        service.save_skill(
            conn,
            "panels",
            {"description": "saved despite file failure"},
            now=1,
            database_parent=tmp_path,
        )
    assert error.value.code == ErrorCode.skill_save_incomplete
    assert error.value.detail == {
        "skill_name": "panels",
        "saved": True,
        "published": False,
        "phase": "file",
    }
    assert "Read the canonical skill before retry" in error.value.message
    assert path.read_bytes() == old_bytes
    source = read_skill_source(conn, "panels")
    assert 'description: "saved despite file failure"' in source
    write_skill_home(conn, tmp_path)
    assert path.read_text() == source
    monkeypatch.setattr(service, "atomic_replace_text", original_replace)
    repaired = service.save_skill(
        conn,
        "panels",
        {"markdown_body": "# Retry from current row"},
        now=2,
        database_parent=tmp_path,
    )
    assert repaired.description == "saved despite file failure"
    assert path.read_text() == repaired.source_text


def test_reviewed_skill_batch_is_atomic_and_retains_original_versions(
    tmp_path: Path, database: sqlite3.Connection
) -> None:
    import hashlib

    reconcile_managed_skill_versions(database)
    before = read_skill_source(database, "panels")
    original_versions = list(database.execute("SELECT id,content FROM managed_skill_versions"))
    target = before + "\nOwner custom text remains exact.\n"
    created = "---\nname: reviewed-manager\ndescription: New role\n---\n\nFull source.\n"
    batch: dict[str, Any] = {
        "skills": [
            {
                "name": "panels",
                "source_text": target,
                "expected_sha256": hashlib.sha256(before.encode()).hexdigest(),
            },
            {"name": "reviewed-manager", "source_text": created, "expected_sha256": "0" * 64},
        ]
    }
    with pytest.raises(PlannerError, match="hash changed"):
        service.apply_skills(database, batch, now=21, database_parent=tmp_path)
    assert read_skill_source(database, "panels") == before
    assert not database.execute(
        "SELECT 1 FROM managed_skills WHERE skill_name='reviewed-manager'"
    ).fetchone()
    batch["skills"][1]["expected_sha256"] = None
    service.apply_skills(database, batch, now=21, database_parent=tmp_path)
    assert read_skill_source(database, "panels") == target
    assert (tmp_path / "skills/reviewed-manager/SKILL.md").read_text() == created
    for row in original_versions:
        assert (
            database.execute(
                "SELECT content FROM managed_skill_versions WHERE id=?", (row[0],)
            ).fetchone()[0]
            == row[1]
        )


def test_full_skill_batch_retry_republishes_only_the_exact_applied_target(
    tmp_path: Path, database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    before = read_skill_source(database, "panels")
    target = before + "\nReviewed source.\n"
    batch: dict[str, Any] = {
        "skills": [
            {
                "name": "panels",
                "source_text": target,
                "expected_sha256": hashlib.sha256(before.encode()).hexdigest(),
            }
        ]
    }
    writer = atomic_replace_text

    def fail_publication(*args: object) -> None:
        raise OSError("publication unavailable")

    monkeypatch.setattr(service, "atomic_replace_text", fail_publication)
    with pytest.raises(PlannerError) as failed:
        service.apply_skills(database, batch, now=21, database_parent=tmp_path)
    assert failed.value.code is ErrorCode.skill_save_incomplete
    monkeypatch.setattr(service, "atomic_replace_text", writer)
    service.apply_skills(database, batch, now=22, database_parent=tmp_path)
    assert (
        database.execute(
            "SELECT updated_at FROM managed_skills WHERE skill_name='panels'"
        ).fetchone()[0]
        == 21
    )
    assert (tmp_path / "skills/panels/SKILL.md").read_text() == target
    service.save_skill(
        database,
        "panels",
        {"markdown_body": "Later owner decision."},
        now=23,
        database_parent=tmp_path,
    )
    with pytest.raises(PlannerError, match="hash changed"):
        service.apply_skills(database, batch, now=24, database_parent=tmp_path)


def test_manager_skill_primary_identity_matches_the_generated_role() -> None:
    from planner.managed_skills import PACKAGED_SKILLS_ROOT
    from planner.runtime.logic.conversation_start_resolution import manager_role_materials

    role = manager_role_materials("si_contract")
    identity = dict(role.identity_environment_variables)
    assert identity == {"PLAN_ACTOR": "manager", "PLAN_MANAGER_ID": "si_contract"}
    source = (PACKAGED_SKILLS_ROOT / "panels-manager/SKILL.md").read_text()
    primary = next(line for line in source.splitlines() if line.startswith("You supervise"))
    assert "PLAN_MANAGER_ID" in primary
    assert "PLAN_SPRINT_ITEM_ID" not in primary and "PLAN\\_SPRINT" not in primary
    assert "If `PLAN_MANAGER_ID` is absent" in source
