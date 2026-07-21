from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from planner.environments.fake_fixture import (
    FAKE_FIXTURE_VERSION,
    build_fake_environment_database,
)
from planner.environments.materialize import (
    inspect_environment_instance,
    prepare_environment_instance,
    reset_environment_instance,
)
from planner.worker_types.configuration import configured_worker_type_registry


def test_fake_fixture_builds_current_schema_with_registered_worker_types(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fixture.db"

    report = build_fake_environment_database(db_path, now=1_800_000_000)

    assert report.fixture_version == FAKE_FIXTURE_VERSION
    assert report.logical_summary == {
        "projects": 2,
        "sprints": 1,
        "sprint_items": 2,
        "days": 1,
        "tickets": 4,
        "managed_files": 2,
    }
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] > 0
        worker_types = {
            str(row["worker_type"])
            for row in conn.execute("SELECT DISTINCT worker_type FROM tickets")
        }
        registry = configured_worker_type_registry()
        assert len(worker_types) >= 3
        for worker_type in worker_types:
            registry.require(worker_type)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='worker_types'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM day_tickets").fetchone()[0]
            == report.logical_summary["tickets"]
        )
    finally:
        conn.close()

    for relative_path in report.managed_file_relative_paths:
        managed_file = db_path.parent / "files" / relative_path
        assert managed_file.is_file()
        assert managed_file.read_bytes()
    assert all(path.parts[0] == "tickets" for path in report.managed_file_relative_paths)


def test_fake_fixture_is_same_logical_seed_but_independent_ids_and_database_bytes(
    tmp_path: Path,
) -> None:
    first_db_path = tmp_path / "first" / "planner.db"
    second_db_path = tmp_path / "second" / "planner.db"

    first = build_fake_environment_database(first_db_path, now=1_800_000_000)
    second = build_fake_environment_database(second_db_path, now=1_800_000_000)

    assert first.fixture_version == second.fixture_version == FAKE_FIXTURE_VERSION
    assert first.logical_summary == second.logical_summary
    assert first.logical_titles == second.logical_titles
    assert first.generated_ids.isdisjoint(second.generated_ids)
    assert first_db_path != second_db_path
    assert _sha256(first_db_path) != _sha256(second_db_path)


def test_prepare_materializes_staging_and_previews_as_independent_instances(
    tmp_path: Path,
) -> None:
    staging_repository_root = _repository_root(tmp_path, "staging-repo")
    first_preview_repository_root = _repository_root(tmp_path, "feature-one-repo")
    second_preview_repository_root = _repository_root(tmp_path, "feature-two-repo")
    environment_root = _short_environment_root(tmp_path)

    staging = prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(staging_repository_root,),
    )
    first_preview = prepare_environment_instance(
        kind="preview",
        instance_id="feature-one",
        environment_root=environment_root,
        port=9011,
        repository_roots=(first_preview_repository_root,),
    )
    second_preview = prepare_environment_instance(
        kind="preview",
        instance_id="feature-two",
        environment_root=environment_root,
        port=9012,
        repository_roots=(second_preview_repository_root,),
    )

    assert staging.fixture_version == FAKE_FIXTURE_VERSION
    assert first_preview.fixture_version == FAKE_FIXTURE_VERSION
    assert second_preview.fixture_version == FAKE_FIXTURE_VERSION
    assert len({staging.db_path, first_preview.db_path, second_preview.db_path}) == 3
    assert len(
        {
            staging.managed_files_root,
            first_preview.managed_files_root,
            second_preview.managed_files_root,
        }
    ) == 3
    assert len({staging.hermes_home, first_preview.hermes_home, second_preview.hermes_home}) == 3
    assert len({staging.logs_dir, first_preview.logs_dir, second_preview.logs_dir}) == 3
    assert (
        len(
            {
                staging.dispatcher_lock_path,
                first_preview.dispatcher_lock_path,
                second_preview.dispatcher_lock_path,
            }
        )
        == 3
    )
    assert (
        len(
            {
                staging.server_control_socket_path,
                first_preview.server_control_socket_path,
                second_preview.server_control_socket_path,
            }
        )
        == 3
    )
    assert staging.db_path.is_file()
    assert first_preview.db_path.is_file()
    assert second_preview.db_path.is_file()
    assert _sha256(staging.db_path) != _sha256(first_preview.db_path)
    assert _sha256(first_preview.db_path) != _sha256(second_preview.db_path)
    _assert_skill_only_hermes_home(staging.hermes_home)
    _assert_skill_only_hermes_home(first_preview.hermes_home)
    _assert_skill_only_hermes_home(second_preview.hermes_home)
    assert (staging.instance_root / "manifest.json").is_file()
    assert (first_preview.instance_root / "manifest.json").is_file()
    assert (second_preview.instance_root / "manifest.json").is_file()


def test_live_prepare_creates_empty_layout_without_fake_fixture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository_root = _repository_root()

    def fail_fixture(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("live prepare must not build the fake fixture")

    monkeypatch.setattr(
        "planner.environments.materialize.build_fake_environment_database",
        fail_fixture,
    )

    live = prepare_environment_instance(
        kind="live",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(repository_root,),
    )

    assert live.fixture_version is None
    assert not live.db_path.exists()
    assert live.managed_files_root.is_dir()
    _assert_skill_only_hermes_home(live.hermes_home)


def test_reset_rebuilds_fake_state_and_preserves_instance_identity(tmp_path: Path) -> None:
    repository_root = _repository_root()
    staging = prepare_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(repository_root,),
    )
    marker = staging.db_path.parent / "marker.txt"
    marker.write_text("old data", encoding="utf-8")

    reset = reset_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(repository_root,),
    )
    inspected = inspect_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(repository_root,),
    )

    assert reset.instance_id == staging.instance_id
    assert reset.port == staging.port
    assert reset.hermes_home == staging.hermes_home
    assert reset.credentials_env_file == staging.credentials_env_file
    assert reset.fixture_version == FAKE_FIXTURE_VERSION
    assert inspected.prepared_at == reset.prepared_at
    assert not marker.exists()
    assert reset.db_path.is_file()


def test_failed_reset_keeps_prior_data_tree(tmp_path: Path, monkeypatch) -> None:
    repository_root = _repository_root()
    staging = prepare_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(repository_root,),
    )
    marker = staging.db_path.parent / "marker.txt"
    marker.write_text("old data", encoding="utf-8")

    def fail_fixture(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fixture failed")

    monkeypatch.setattr(
        "planner.environments.materialize.build_fake_environment_database",
        fail_fixture,
    )

    try:
        reset_environment_instance(
            kind="staging",
            environment_root=_short_environment_root(tmp_path),
            repository_roots=(repository_root,),
        )
    except RuntimeError as exc:
        assert str(exc) == "fixture failed"
    else:
        raise AssertionError("reset should have failed")

    assert staging.db_path.is_file()
    assert marker.read_text(encoding="utf-8") == "old data"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repository_root(tmp_path: Path | None = None, name: str = "repo") -> Path:
    if tmp_path is None:
        return Path(__file__).resolve().parents[2]
    repository_root = tmp_path / name
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root


def _short_environment_root(tmp_path: Path) -> Path:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return Path("/tmp") / f"pe-fixture-{os.getpid()}-{digest}"


def _assert_skill_only_hermes_home(hermes_home: Path) -> None:
    entries = {path.name for path in hermes_home.iterdir()}
    assert entries == {"skills"}
    assert not (hermes_home / "auth.json").exists()
    assert not (hermes_home / "config.json").exists()
    assert not (hermes_home / "sessions").exists()
    for skill_link in (hermes_home / "skills").iterdir():
        assert skill_link.is_symlink()
