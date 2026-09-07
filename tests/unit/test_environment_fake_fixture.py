from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from planner.environments.fake_fixture import (
    FAKE_FIXTURE_VERSION,
    build_fake_environment_database,
)
from planner.environments.materialize import (
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
        assert conn.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 1
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


def test_failed_reset_keeps_prior_data_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = _repository_root()
    staging = prepare_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        repository_roots=(repository_root,),
    )
    marker = staging.db_path.parent / "marker.txt"
    marker.write_text("old data", encoding="utf-8")

    def fail_fixture(*args: object, **kwargs: object) -> None:
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
