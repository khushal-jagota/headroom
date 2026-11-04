"""Bootstrap refuses unsafe snapshots and preserves exact canonical/account bytes."""

from __future__ import annotations

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from planner.conversation.backends.contracts import BackendSpawnFailed
from planner.executor.configuration import ExecutorPaths
from planner.executor.protocol import CanonicalSkillSnapshot, CanonicalSkillSource
from planner.executor.skills import (
    install_native_skill_snapshot,
    read_canonical_skill_snapshot,
)


def _source(name: str, body: str = "Canonical café\r\n") -> CanonicalSkillSource:
    return CanonicalSkillSource(
        name, f'---\r\nname: "{name}"\r\ndescription: exact\r\n---\r\n{body}'
    )


def _snapshot(*extras: CanonicalSkillSource) -> CanonicalSkillSnapshot:
    return CanonicalSkillSnapshot(
        (_source("panels"), _source("panels-worker"), _source("specialist"), *extras),
        ("panels", "panels-worker", "specialist"),
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        CanonicalSkillSnapshot((_source("panels"),), ("panels", "panels-worker")),
        _snapshot(_source("../escape")),
        _snapshot(_source("PANELS")),
        _snapshot(CanonicalSkillSource("bad", _source("other").source_text)),
    ],
)
def test_invalid_snapshot_refuses_before_any_owned_or_home_write(
    tmp_path: Path, snapshot: CanonicalSkillSnapshot
) -> None:
    paths = ExecutorPaths(tmp_path / "executor")
    home = tmp_path / "account"
    with pytest.raises(BackendSpawnFailed):
        install_native_skill_snapshot(snapshot, paths=paths, native_home=home)
    assert not paths.root.exists()
    assert not home.exists()


def test_exact_bytes_and_full_generation_reuse(tmp_path: Path) -> None:
    paths = ExecutorPaths(tmp_path / "executor")
    snapshot = _snapshot(_source("optional", "custom source\r\n\r\n"))
    home = tmp_path / "account"
    generation = install_native_skill_snapshot(snapshot, paths=paths, native_home=home)
    assert generation.parent == paths.skills
    for source in snapshot.sources:
        expected = source.source_text.encode("utf-8")
        assert (generation / "skills" / source.skill_name / "SKILL.md").read_bytes() == expected
        assert (home / "skills" / source.skill_name / "SKILL.md").read_bytes() == expected
    assert install_native_skill_snapshot(snapshot, paths=paths, native_home=home) == generation
    assert len([item for item in paths.skills.iterdir() if item.is_dir()]) == 1


def test_stale_broken_and_removed_links_preserve_account_and_external_content(
    tmp_path: Path,
) -> None:
    paths = ExecutorPaths(tmp_path / "executor")
    home = tmp_path / "account"
    skills = home / "skills"
    (skills / "custom").mkdir(parents=True)
    (skills / "custom" / "SKILL.md").write_bytes(b"custom")
    (home / "auth.json").write_bytes(b"credentials")
    external = tmp_path / "external"
    external.mkdir()
    (external / "SKILL.md").write_bytes(b"external canonical collision")
    (skills / "panels").symlink_to(external, target_is_directory=True)
    (skills / "panels-worker").symlink_to(tmp_path / "missing")
    (skills / "unrelated-broken").symlink_to(tmp_path / "missing")
    old = install_native_skill_snapshot(
        _snapshot(_source("obsolete")), paths=paths, native_home=home
    )
    # Only links into this executor's generations are obsolete executor content.
    (skills / "unrelated-external").symlink_to(external)
    current = _snapshot(_source("added"))
    install_native_skill_snapshot(current, paths=paths, native_home=home)
    assert not (skills / "obsolete").is_symlink()
    assert (old / "skills" / "obsolete" / "SKILL.md").exists()
    assert (skills / "unrelated-broken").is_symlink()
    assert (skills / "unrelated-external").resolve() == external
    assert (external / "SKILL.md").read_bytes() == b"external canonical collision"
    assert (skills / "custom" / "SKILL.md").read_bytes() == b"custom"
    assert (home / "auth.json").read_bytes() == b"credentials"


def test_legacy_whole_root_link_preserves_unrelated_entries_and_target(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    (legacy / "custom").mkdir(parents=True)
    (legacy / "custom" / "SKILL.md").write_bytes(b"keep")
    (legacy / "panels").mkdir()
    (legacy / "panels" / "SKILL.md").write_bytes(b"old")
    home = tmp_path / "account"
    home.mkdir()
    (home / "skills").symlink_to(legacy)
    install_native_skill_snapshot(
        _snapshot(), paths=ExecutorPaths(tmp_path / "executor"), native_home=home
    )
    assert not (home / "skills").is_symlink()
    assert (home / "skills" / "custom" / "SKILL.md").read_bytes() == b"keep"
    assert (legacy / "panels" / "SKILL.md").read_bytes() == b"old"


@pytest.mark.parametrize("ancestor", ["root", "skills", "lock", "generation", "source"])
def test_symlink_in_owned_tree_is_refused_without_external_writes(
    tmp_path: Path, ancestor: str
) -> None:
    paths = ExecutorPaths(tmp_path / "executor")
    external = tmp_path / "external"
    external.mkdir()
    paths.root.mkdir()
    if ancestor == "root":
        paths.root.rmdir()
        paths.root.symlink_to(external)
    elif ancestor == "skills":
        paths.skills.symlink_to(external)
    elif ancestor == "lock":
        paths.skills.mkdir()
        (paths.skills / ".install.lock").symlink_to(external / "lock")
    else:
        generation = install_native_skill_snapshot(
            _snapshot(), paths=paths, native_home=tmp_path / "first"
        )
        if ancestor == "generation":
            shutil.rmtree(generation)
            generation.symlink_to(external)
        else:
            source = generation / "skills" / "panels" / "SKILL.md"
            source.unlink()
            source.symlink_to(external / "SKILL.md")
    with pytest.raises(BackendSpawnFailed):
        install_native_skill_snapshot(_snapshot(), paths=paths, native_home=tmp_path / "second")
    assert not list(external.iterdir())
    assert not (tmp_path / "second").exists()


def test_concurrent_snapshots_publish_complete_generations_and_consistent_account(
    tmp_path: Path,
) -> None:
    paths = ExecutorPaths(tmp_path / "executor")
    home = tmp_path / "account"
    snapshots = [
        CanonicalSkillSnapshot(
            tuple(
                _source(name, f"revision {revision}")
                for name in ("panels", "panels-worker", "specialist")
            ),
            _snapshot().required_skill_names,
        )
        for revision in range(5)
    ]
    with ThreadPoolExecutor(max_workers=5) as pool:
        generations = list(
            pool.map(
                lambda item: install_native_skill_snapshot(item, paths=paths, native_home=home),
                snapshots,
            )
        )
    for generation, snapshot in zip(generations, snapshots, strict=True):
        for source in snapshot.sources:
            assert (
                generation / "skills" / source.skill_name / "SKILL.md"
            ).read_bytes() == source.source_text.encode()
    assert (
        len(
            {(home / "skills" / name).resolve().parent for name in _snapshot().required_skill_names}
        )
        == 1
    )
    assert not list(paths.skills.glob(".stage-*"))


def test_tampered_generation_refuses_instead_of_rewriting(tmp_path: Path) -> None:
    paths = ExecutorPaths(tmp_path / "executor")
    generation = install_native_skill_snapshot(
        _snapshot(), paths=paths, native_home=tmp_path / "first"
    )
    changed = generation / "skills" / "panels" / "SKILL.md"
    changed.write_bytes(b"tampered")
    with pytest.raises(BackendSpawnFailed, match="bytes differ"):
        install_native_skill_snapshot(_snapshot(), paths=paths, native_home=tmp_path / "second")
    assert changed.read_bytes() == b"tampered"
    assert not (tmp_path / "second").exists()


def test_worker_snapshot_omits_retired_specialists_except_its_assigned_personal_skill(
    tmp_db: sqlite3.Connection, tmp_path: Path
) -> None:
    from tests.support.retired_workers import seed_historical_worker_type

    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.skill_sources import WORKER_ONLY_SKILL_NAMES
    from planner.tickets import data

    ticket = data.create_ticket(
        tmp_db,
        title="Current worker",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="scripted",
    )
    ordinary = read_canonical_skill_snapshot(tmp_db, ticket.id)
    assert not ({source.skill_name for source in ordinary.sources} & WORKER_ONLY_SKILL_NAMES)
    # Restore a historical fixture directly. New personal creation stays forbidden.
    seed_historical_worker_type(tmp_db, "personal")
    from tests.support.ticket_workflows import preset_workflow_json

    tmp_db.execute("UPDATE tickets SET worker_type='personal', workflow_json=? WHERE id=?",
                   (preset_workflow_json(tmp_db, "personal"), ticket.id))
    before = tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone()
    personal = read_canonical_skill_snapshot(tmp_db, ticket.id)
    assert {source.skill_name for source in personal.sources} & WORKER_ONLY_SKILL_NAMES == {
        "panels-worker-personal-task"
    }
    home = tmp_path / "account"
    paths = ExecutorPaths(tmp_path / "executor")
    personal_generation = install_native_skill_snapshot(personal, paths=paths, native_home=home)
    assert (home / "skills/panels-worker-personal-task/SKILL.md").is_file()
    install_native_skill_snapshot(ordinary, paths=paths, native_home=home)
    assert not (home / "skills/panels-worker-personal-task").exists()
    assert (personal_generation / "skills/panels-worker-personal-task/SKILL.md").is_file()
    assert tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone() == before


def test_required_skills_follow_saved_ticket_workflow(tmp_path: Path) -> None:
    from dataclasses import replace

    from tests.support.principals import OWNER_PRINCIPAL

    from planner.core.db import connect, create_schema
    from planner.executor.skills import read_canonical_skill_snapshot
    from planner.tickets.data import create_ticket
    from planner.worker_types.configuration import configured_worker_type_registry
    from planner.worker_types.store import workflow_from_preset, write_definition

    with closing(connect(str(tmp_path / "skills.db"))) as conn:
        create_schema(conn)
        preset = configured_worker_type_registry().require("coding")
        normal = create_ticket(
            conn,
            worker_type="coding",
            title="Preset",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        )
        custom = create_ticket(
            conn,
            worker_type=None,
            workflow=replace(
                workflow_from_preset(preset),
                specialist_skill=None,
            ),
            title="Custom",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            employee_backend="codex",
            employee_launch_model="gpt-6.1-sol",
        )
        assert read_canonical_skill_snapshot(conn, custom.id).required_skill_names == (
            "panels",
            "panels-worker",
        )
        replacement = (
            configured_worker_type_registry().require("general").worker_profile.specialist_skill
        )
        write_definition(
            conn,
            replace(
                preset,
                worker_profile=replace(
                    preset.worker_profile,
                    specialist_skill=replacement,
                ),
            ),
            now=2,
        )
        assert read_canonical_skill_snapshot(conn, normal.id).required_skill_names == (
            "panels",
            "panels-worker",
            preset.worker_profile.specialist_skill,
        )
