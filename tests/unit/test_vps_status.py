from __future__ import annotations

import json
import os
import shutil
import sqlite3
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
    apply_cleanup_inventory,
    collect_cleanup_inventory,
    collect_vps_status,
    collect_vps_status_summary,
)


class _StatVfs:
    f_frsize = 4096
    f_blocks = 1_000_000
    f_bavail = 500_000


def test_small_summary_collects_linux_cpu_ram_disk_and_backup_with_boundaries(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(config.backup_dir)
    backup_dir.mkdir()
    source = Path(config.db_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
    create_database_backup(source, backup_dir, "revision")
    proc_stat = iter(
        (
            "cpu 100 0 100 800 0 0 0 0\n",
            "cpu 180 0 180 840 0 0 0 0\n",
        )
    )

    def read_text(path: Path) -> str:
        if path == Path("/proc/stat"):
            return next(proc_stat)
        if path == Path("/proc/meminfo"):
            return "MemTotal: 1000 kB\nMemAvailable: 200 kB\n"
        return path.read_text(encoding="utf-8")

    summary = collect_vps_status_summary(
        config,
        deployment_outcome="failed",
        deployment_detail="candidate health proof failed",
        dependencies=VpsStatusDependencies(
            now=lambda: datetime.now(UTC),
            platform_name=lambda: "Linux",
            statvfs=lambda _path: _StatVfs(),  # type: ignore[return-value, arg-type]
            read_text=read_text,
            sleep=lambda _: None,
        ),
    ).as_dict()

    assert summary["deployed_sha"] == config.app_sha
    assert summary["deployment"] == {
        "outcome": "failed",
        "detail": "candidate health proof failed",
    }
    assert summary["cpu"] == {
        "used_percent": 80.0,
        "state": "warning",
        "unavailable_reason": None,
    }
    assert summary["ram"] == {
        "used_percent": 80.0,
        "state": "warning",
        "unavailable_reason": None,
    }
    assert summary["disk"] == {
        "used_percent": 50.0,
        "state": "healthy",
        "unavailable_reason": None,
    }
    assert isinstance(summary["backup"]["age_seconds"], int)  # type: ignore[index]


@pytest.mark.parametrize(
    ("stat_samples", "meminfo", "cpu_reason", "ram_reason"),
    [
        (
            ("not cpu\n", "not cpu\n"),
            "",
            "CPU evidence is unavailable",
            "RAM evidence is unavailable",
        ),
        (
            ("cpu 10 0 10 80\n", "cpu 5 0 5 40\n"),
            "MemTotal: 0 kB\nMemAvailable: 0 kB\n",
            "CPU counters did not advance safely",
            "RAM evidence is unavailable",
        ),
    ],
)
def test_small_summary_keeps_malformed_and_reset_proc_evidence_explicitly_unavailable(
    tmp_path: Path,
    stat_samples: tuple[str, str],
    meminfo: str,
    cpu_reason: str,
    ram_reason: str,
) -> None:
    config = _config(tmp_path)
    Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
    samples = iter(stat_samples)

    def read_text(path: Path) -> str:
        return next(samples) if path == Path("/proc/stat") else meminfo

    summary = collect_vps_status_summary(
        config,
        dependencies=VpsStatusDependencies(
            platform_name=lambda: "Linux",
            read_text=read_text,
            sleep=lambda _: None,
        ),
    )

    assert summary.cpu == {
        "used_percent": None,
        "state": "unavailable",
        "unavailable_reason": cpu_reason,
    }
    assert summary.ram == {
        "used_percent": None,
        "state": "unavailable",
        "unavailable_reason": ram_reason,
    }


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
