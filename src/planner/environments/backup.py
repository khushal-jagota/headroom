"""Small, operator-facing SQLite backup and restore operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_DATABASE_NAME = "database.sqlite"
SNAPSHOT_METADATA_NAME = "metadata.json"
SNAPSHOT_FORMAT = "panels-sqlite-backup-v1"
RETENTION_COUNT = 7


def create_database_backup(
    source_db: Path,
    backup_dir: Path,
    deployed_revision: str,
) -> Path:
    """Create and publish one verified online SQLite snapshot."""
    source_db = source_db.expanduser().resolve()
    backup_dir = backup_dir.expanduser().resolve()
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    backup_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=".backup-", dir=backup_dir))
    try:
        temporary_database = temporary_dir / SNAPSHOT_DATABASE_NAME
        with sqlite3.connect(source_db) as source_connection, sqlite3.connect(
            temporary_database
        ) as destination_connection:
            source_connection.backup(destination_connection)
        _verify_database(temporary_database)
        checksum = _sha256(temporary_database)
        metadata = {
            "format": SNAPSHOT_FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "deployed_revision": deployed_revision,
            "sha256": checksum,
            "verified": True,
        }
        (temporary_dir / SNAPSHOT_METADATA_NAME).write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n"
        )
        snapshot_name = (
            "snapshot-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        snapshot = backup_dir / snapshot_name
        os.replace(temporary_dir, snapshot)
        _retain_verified_snapshots(backup_dir)
        return snapshot
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def restore_database_snapshot(
    snapshot: Path,
    destination_db: Path,
    *,
    live_stopped: bool,
) -> None:
    """Restore one verified snapshot into a stopped live database."""
    if not live_stopped:
        raise ValueError("live environment must be stopped before restore")
    snapshot = snapshot.expanduser().resolve()
    destination_db = destination_db.expanduser().resolve()
    metadata_path = snapshot / SNAPSHOT_METADATA_NAME
    snapshot_database = snapshot / SNAPSHOT_DATABASE_NAME
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("snapshot is not verified") from exc
    if not isinstance(metadata, dict) or (
        metadata.get("format") != SNAPSHOT_FORMAT
        or metadata.get("verified") is not True
        or metadata.get("sha256") != _sha256(snapshot_database)
    ):
        raise ValueError("snapshot is not verified")
    _verify_database(snapshot_database)
    destination_db.parent.mkdir(parents=True, exist_ok=True)
    temporary_database = destination_db.with_name(
        f".{destination_db.name}.restore-tmp-{uuid.uuid4().hex}"
    )
    sidecar_backups: list[tuple[Path, Path]] = []
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(destination_db) + suffix)
        if sidecar.exists():
            sidecar_backups.append(
                (sidecar, sidecar.with_name(f".{sidecar.name}.restore-old-{uuid.uuid4().hex}"))
            )
    try:
        shutil.copy2(snapshot_database, temporary_database)
        _verify_database(temporary_database)
        try:
            for sidecar, sidecar_backup in sidecar_backups:
                os.replace(sidecar, sidecar_backup)
            os.replace(temporary_database, destination_db)
        except BaseException:
            for sidecar, sidecar_backup in reversed(sidecar_backups):
                if sidecar_backup.exists() and not sidecar.exists():
                    os.replace(sidecar_backup, sidecar)
            raise
        for _, sidecar_backup in sidecar_backups:
            sidecar_backup.unlink(missing_ok=True)
    finally:
        temporary_database.unlink(missing_ok=True)


def _verify_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed for {database}")


def _sha256(database: Path) -> str:
    digest = hashlib.sha256()
    with database.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_snapshots(backup_dir: Path) -> list[Path]:
    snapshots: list[tuple[Path, str]] = []
    for candidate in backup_dir.glob("snapshot-*"):
        metadata_path = candidate / SNAPSHOT_METADATA_NAME
        database_path = candidate / SNAPSHOT_DATABASE_NAME
        if not candidate.is_dir() or not metadata_path.is_file() or not database_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(metadata, dict) and (
            metadata.get("format") == SNAPSHOT_FORMAT
            and metadata.get("verified") is True
            and metadata.get("sha256") == _sha256(database_path)
        ):
            created_at = metadata.get("created_at")
            snapshots.append((candidate, created_at if isinstance(created_at, str) else ""))
    return [
        path
        for path, _ in sorted(snapshots, key=lambda item: (item[1], item[0].name), reverse=True)
    ]


def _retain_verified_snapshots(backup_dir: Path) -> None:
    excess = _verified_snapshots(backup_dir)[RETENTION_COUNT:]
    if not excess:
        return
    # This writer adds one snapshot at a time to a directory it normally keeps at seven,
    # so one successful removal restores the invariant without copying a database merely
    # to delete it. If that removal fails, no older recovery point has been touched.
    shutil.rmtree(excess[-1])
