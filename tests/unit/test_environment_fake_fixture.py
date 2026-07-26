from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

from planner.environments import materialize as environment_materialize
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


def test_prepare_common_layout_materializes_only_runtime_state_directories(
    tmp_path: Path,
) -> None:
    instance = SimpleNamespace(
        instance_root=tmp_path / "instance",
        logs_dir=tmp_path / "logs",
        dispatcher_lock_path=tmp_path / "locks" / "dispatcher.lock",
        server_control_socket_path=tmp_path / "run" / "server.sock",
        db_path=(tmp_path / "instance-data" / "planning.db"),
        allowed_repository_roots=(Path(__file__).resolve().parents[2],),
    )

    environment_materialize._prepare_common_layout(instance)

    assert instance.instance_root.is_dir()
    assert instance.logs_dir.is_dir()
    assert instance.dispatcher_lock_path.parent.is_dir()
    assert instance.server_control_socket_path.parent.is_dir()


def test_live_prepare_records_contract_without_fake_fixture_or_persistent_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_fixture(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("live prepare must not build the fake fixture")

    monkeypatch.setattr(
        "planner.environments.materialize.build_fake_environment_database",
        fail_fixture,
    )

    live = prepare_environment_instance(
        kind="live",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(),
    )

    assert live.fixture_version is None
    assert live.instance_root == _short_environment_root(tmp_path).resolve()
    assert live.db_path == live.instance_root / "current" / "data" / "planner.db"
    assert live.logs_dir == live.instance_root / "current" / "logs"
    assert not live.db_path.exists()
    assert not live.managed_files_root.exists()

    inspected = inspect_environment_instance(
        kind="live",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(),
    )
    assert inspected == live


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
    assert reset.port_policy == staging.port_policy
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
    subprocess.run(["git", "init", "-q", str(repository_root)], check=True)
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "src" / "planner" / "skills",
        repository_root / "src" / "planner" / "skills",
    )
    return repository_root


def _short_environment_root(tmp_path: Path) -> Path:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return Path("/tmp") / f"pe-fixture-{os.getpid()}-{digest}"
