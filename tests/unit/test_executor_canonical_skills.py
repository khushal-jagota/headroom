"""Bootstrap refuses unsafe snapshots and preserves exact canonical/account bytes."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from planner.conversation.backends.contracts import BackendSpawnFailed
from planner.core.db import connect, create_schema
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import CanonicalSkillSnapshot, CanonicalSkillSource
from planner.executor.skills import (
    install_native_skill_snapshot,
    read_canonical_skill_snapshot,
    validate_skill_snapshot,
)
from planner.worker_types.shipped import shipped_worker_types


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
        CanonicalSkillSnapshot((_source("panels"),), ("panels",)),
        _snapshot(_source("../escape")),
        _snapshot(_source("panels")),
        _snapshot(_source("PANELS")),
        _snapshot(CanonicalSkillSource("bad", "")),
        _snapshot(CanonicalSkillSource("bad", _source("other").source_text)),
        _snapshot(_source("bad", "\ud800")),
        CanonicalSkillSnapshot(
            (*_snapshot().sources, _source("panels-rollover")),
            (*_snapshot().required_skill_names, "panels-rollover"),
        ),
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


def test_reader_uses_database_bytes_and_registered_specialist_without_committing(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "planner.db"))
    try:
        create_schema(conn)
        conn.execute("BEGIN")
        # The helper reads only this assigned Ticket field. Keep this fixture local
        # rather than coupling a snapshot test to ordinary Ticket creation defaults.
        conn.execute("CREATE TEMP TABLE tickets(id TEXT, worker_type TEXT)")
        definition = shipped_worker_types()[0]
        worker_type = str(definition["worker_type"])
        conn.execute("INSERT INTO temp.tickets VALUES ('assigned', ?)", (worker_type,))
        exact = _source("panels", "database-only café\r\n\r\n").source_text
        conn.execute(
            "UPDATE managed_skills SET source_text = ? WHERE skill_name = 'panels'", (exact,)
        )
        assert conn.in_transaction
        snapshot = read_canonical_skill_snapshot(conn, "assigned")
        assert conn.in_transaction
        assert (
            next(source.source_text for source in snapshot.sources if source.skill_name == "panels")
            == exact
        )
        profile = definition["profile"]
        assert isinstance(profile, dict)
        assert profile["specialist_skill"] in snapshot.required_skill_names
        validate_skill_snapshot(snapshot)
        conn.execute("DELETE FROM managed_skills WHERE skill_name = 'panels-worker'")
        with pytest.raises(BackendSpawnFailed, match="absent"):
            read_canonical_skill_snapshot(conn, "assigned")
        with pytest.raises(BackendSpawnFailed, match="does not exist"):
            read_canonical_skill_snapshot(conn, "unknown")
    finally:
        conn.close()


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


def test_account_install_failure_is_backend_refusal(tmp_path: Path) -> None:
    home = tmp_path / "account"
    home.mkdir()
    (home / "skills").write_bytes(b"not a directory")
    with pytest.raises(BackendSpawnFailed):
        install_native_skill_snapshot(
            _snapshot(), paths=ExecutorPaths(tmp_path / "executor"), native_home=home
        )
    assert (home / "skills").read_bytes() == b"not a directory"


def test_retired_source_bytes_stay_in_snapshot_without_native_exposure(tmp_path: Path) -> None:
    snapshot = _snapshot(_source("panels-rollover"))
    home = tmp_path / "account"
    generation = install_native_skill_snapshot(
        snapshot, paths=ExecutorPaths(tmp_path / "executor"), native_home=home
    )
    assert (
        generation / "skills" / "panels-rollover" / "SKILL.md"
    ).read_bytes() == snapshot.sources[-1].source_text.encode()
    assert not (home / "skills" / "panels-rollover").exists()
