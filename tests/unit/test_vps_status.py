from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from planner.core.config import Config, load_config
from planner.environments.backup import create_database_backup
from planner.environments.vps_status import (
    VpsStatusDependencies,
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
