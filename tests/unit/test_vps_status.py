from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import Config, load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.environments.backup import create_database_backup
from planner.environments.cli import EnvironmentCliDependencies, environment
from planner.environments.vps_status import (
    VpsStatusDependencies,
    VpsStatusPolicy,
    VpsStatusSnapshot,
    apply_cleanup_inventory,
    collect_cleanup_inventory,
    collect_vps_status,
)


class _StatVfs:
    f_frsize = 4096
    f_blocks = 1_000_000
    f_bavail = 500_000


def _config(tmp_path: Path) -> Config:
    return load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_BACKUP_DIR": str(tmp_path / "backups"),
            "PLAN_APP_SHA": "0123456789abcdef0123456789abcdef01234567",
        },
    )


def test_snapshot_has_one_json_safe_shape_and_mac_resources_are_honestly_unavailable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    snapshot = collect_vps_status(
        config,
        dependencies=VpsStatusDependencies(platform_name=lambda: "Darwin"),
    )

    payload = snapshot.as_dict()
    assert list(payload) == [
        "collected_at",
        "overall_state",
        "environment",
        "app",
        "backup",
        "disk",
        "workloads",
        "worktrees",
        "logs",
        "cleanup_candidates",
        "resources",
    ]
    assert json.loads(json.dumps(payload)) == payload
    assert payload["resources"] == {
        "state": "unavailable",
        "summary": "resource collection is unavailable on macOS",
        "cpu_percent": None,
        "load_averages": None,
        "ram": None,
        "swap": None,
    }


def test_snapshot_does_not_serialise_credentials_or_process_arguments(tmp_path: Path) -> None:
    config = _config(tmp_path)
    secret = "api-key-should-never-be-visible"
    secret_worktree = tmp_path / secret
    secret_worktree.mkdir()
    snapshot = collect_vps_status(
        config,
        dependencies=VpsStatusDependencies(
            platform_name=lambda: "Darwin",
            process_lines=lambda: [f"123 1 00:01 planner --token {secret} " + "x" * 10000],
            git_worktree_output=lambda _root: f"worktree {secret_worktree}\n",
        ),
        application_root=tmp_path,
    )

    encoded = json.dumps(snapshot.as_dict())
    assert secret not in encoded
    assert "x" * 1000 not in encoded


def test_cleanup_apply_reproves_and_retains_target_that_changed_after_inventory(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    backup_dir = Path(config.backup_dir)
    temporary = backup_dir / ".backup-abandoned"
    temporary.mkdir(parents=True)
    old = datetime.now(UTC) - timedelta(hours=25)
    temporary.touch()
    import os

    os.utime(temporary, (old.timestamp(), old.timestamp()))
    inventory = collect_cleanup_inventory(config)
    assert [candidate.kind for candidate in inventory.candidates] == ["temporary_backup"]

    temporary.rmdir()
    temporary.mkdir()
    result = apply_cleanup_inventory(inventory)

    assert temporary.exists()
    assert result.review_needed == [str(temporary)]


def test_cleanup_inventory_uses_verified_backup_retention_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = Path(config.db_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
    for revision in range(8):
        create_database_backup(source, Path(config.backup_dir), str(revision))
    verified = next(Path(config.backup_dir).glob("snapshot-*"))
    shutil.copytree(verified, Path(config.backup_dir) / "snapshot-extra-verified")
    ambiguous = Path(config.backup_dir) / "snapshot-ambiguous"
    ambiguous.mkdir()

    inventory = collect_cleanup_inventory(config)

    assert [candidate.kind for candidate in inventory.candidates] == ["backup_retention"]
    assert ambiguous.exists()


def test_symlinked_snapshot_evidence_is_absent_from_status_and_cleanup(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = Path(config.db_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
    regular = create_database_backup(source, Path(config.backup_dir), "regular")
    external = create_database_backup(source, tmp_path / "external", "external")
    (Path(config.backup_dir) / "snapshot-external-link").symlink_to(
        external, target_is_directory=True
    )
    linked_metadata = Path(config.backup_dir) / "snapshot-linked-metadata"
    shutil.copytree(regular, linked_metadata)
    (linked_metadata / "metadata.json").unlink()
    os.symlink(external / "metadata.json", linked_metadata / "metadata.json")
    linked_database = Path(config.backup_dir) / "snapshot-linked-database"
    shutil.copytree(regular, linked_database)
    (linked_database / "database.sqlite").unlink()
    os.symlink(external / "database.sqlite", linked_database / "database.sqlite")

    snapshot = collect_vps_status(
        config,
        dependencies=VpsStatusDependencies(platform_name=lambda: "Darwin"),
    )
    inventory = collect_cleanup_inventory(config)

    assert regular.exists()
    assert snapshot.backup["verified_snapshot_count"] == 1
    assert inventory.candidates == ()
    assert apply_cleanup_inventory(inventory).removed == []
    assert external.exists()
    assert (Path(config.backup_dir) / "snapshot-external-link").is_symlink()


def test_cleanup_backup_apply_retains_a_path_that_is_no_longer_beyond_retention(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source = Path(config.db_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
    for revision in range(8):
        create_database_backup(source, Path(config.backup_dir), str(revision))
    shutil.copytree(
        next(Path(config.backup_dir).glob("snapshot-*")),
        Path(config.backup_dir) / "snapshot-extra-verified",
    )
    inventory = collect_cleanup_inventory(config)
    candidate = inventory.candidates[0].path
    shutil.rmtree(
        next(path for path in Path(config.backup_dir).glob("snapshot-*") if path != candidate)
    )

    result = apply_cleanup_inventory(inventory)

    assert candidate.exists()
    assert result.removed == []
    assert result.review_needed == [str(candidate)]


def test_cleanup_reproof_rejects_a_configured_root_replaced_by_a_symlink(tmp_path: Path) -> None:
    config = _config(tmp_path)
    logs = Path(config.logs_dir)
    logs.mkdir()
    log = logs / "panels.log"
    log.write_text("x", encoding="utf-8")
    inventory = collect_cleanup_inventory(
        config,
        policy=VpsStatusPolicy(log_warning_bytes=1),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    original_logs = tmp_path / "logs-original"
    logs.rename(original_logs)
    logs.symlink_to(outside, target_is_directory=True)

    result = apply_cleanup_inventory(inventory)

    assert (original_logs / "panels.log").exists()
    assert result.rotated == []
    assert result.review_needed == [str(log)]


def test_unreadable_configured_logs_remain_review_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    Path(config.logs_dir).mkdir()

    def unreadable(_root: Path) -> list[Path]:
        raise OSError("permission denied")

    monkeypatch.setattr("planner.environments.vps_status._bounded_children", unreadable)
    snapshot = collect_vps_status(
        config,
        dependencies=VpsStatusDependencies(platform_name=lambda: "Darwin"),
    )

    assert snapshot.logs["state"] == "review_needed"


def test_process_probe_failures_remain_unavailable_in_the_api(tmp_path: Path) -> None:
    config = _config(tmp_path)
    database = connect(config.db_path)
    create_schema(database)
    database.close()

    for failure in (
        subprocess.TimeoutExpired(["ps"], timeout=1),
        subprocess.CalledProcessError(1, ["ps"]),
    ):
        def failing_vps_status_collector(
            _config: Config, failure: BaseException = failure
        ) -> VpsStatusSnapshot:
            return collect_vps_status(
                config,
                dependencies=VpsStatusDependencies(
                    platform_name=lambda: "Darwin",
                    process_lines=lambda: (_ for _ in ()).throw(failure),
                ),
            )

        app = create_app(
            config,
            build_clock(config),
            lambda: connect(config.db_path),
            vps_status_collector=failing_vps_status_collector,
        )
        with TestClient(app) as client:
            response = client.get("/api/vps-status")

        assert response.status_code == 200
        assert response.json()["workloads"] == {
            "state": "unavailable",
            "summary": "process probe failed",
            "items": [],
        }


def test_workloads_recognise_python_module_serve_without_serialising_arguments(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    secret = "workload-secret-must-not-leak"
    snapshot = collect_vps_status(
        config,
        dependencies=VpsStatusDependencies(
            platform_name=lambda: "Darwin",
            process_lines=lambda: [
                f"101 1 00:03 /opt/panels/.venv/bin/python -m planner serve --token {secret}",
                "202 1 00:04 /usr/bin/python3 -m unrelated service",
            ],
        ),
    )

    assert snapshot.workloads == {
        "state": "healthy",
        "summary": "recognised Panels workloads",
        "items": [{"role": "planner", "pid": 101, "state": "running", "age": "00:03"}],
    }
    assert secret not in json.dumps(snapshot.as_dict())


def test_direct_status_cli_and_read_only_api_serialize_the_same_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A worker launched from a deployment can inherit that deployment's PLAN_APP_ROOT.
    # Pin this source-tree equivalence check to the source tree for both call paths.
    monkeypatch.setenv("PLAN_APP_ROOT", str(Path.cwd()))
    config = _config(tmp_path)
    dependencies = VpsStatusDependencies(
        now=lambda: datetime(2026, 7, 24, tzinfo=UTC),
        platform_name=lambda: "Darwin",
        process_lines=lambda: [],
        statvfs=lambda _path: _StatVfs(),  # type: ignore[return-value, arg-type]  # fake stat_result stand-in
    )
    snapshot = collect_vps_status(
        config,
        dependencies=dependencies,
        application_root=Path.cwd(),
    )
    cli = CliRunner().invoke(
        environment,
        ["status", "--json"],
        obj=EnvironmentCliDependencies(
            load_config=lambda: config,
            vps_status_dependencies=dependencies,
        ),
    )
    assert cli.exit_code == 0, cli.output

    database = connect(config.db_path)
    create_schema(database)
    database.close()
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(config.db_path),
        vps_status_collector=lambda _config: snapshot,
    )
    with TestClient(app) as client:
        response = client.get("/api/vps-status")
        assert response.status_code == 200
        assert client.post("/api/vps-status/cleanup").status_code == 404

    assert json.loads(cli.output) == response.json()
