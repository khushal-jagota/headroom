"""Small, operator-facing SQLite backup and restore operations.

A snapshot now protects two things at nightly granularity: the online SQLite
database and the managed-file tree that lives beside it (ticket files,
worker-settings, and the skills home once it exists).  Both parts are verified
before a snapshot is published, both are restored together, and the existing
restraint is unchanged -- verify before publish, atomic publish, retention of
verified snapshots, and existing snapshots left untouched when a run fails.
"""

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

from planner.environments.hermes_home import resolve_planner_home

SNAPSHOT_DATABASE_NAME = "database.sqlite"
SNAPSHOT_METADATA_NAME = "metadata.json"
SNAPSHOT_FILES_DIR_NAME = "files"
SNAPSHOT_FILES_MANIFEST_NAME = "manifest.json"
SNAPSHOT_FORMAT = "panels-backup-v2"
RETENTION_COUNT = 7


def managed_file_roots(database_path: Path) -> dict[str, Path]:
    """The managed-file roots protected alongside the database.

    The managed files root and worker-settings root anchor on the database's
    directory.  The skills home is resolved through the same ``resolve_planner_home``
    the running server uses -- ``PLAN_HERMES_HOME`` when the operator sets it,
    otherwise ``<db parent>/hermes-home`` -- so backup captures exactly the tree
    the server reads.  A root that does not exist yet (the skills home, until it
    becomes canonical managed state) is simply absent here and skipped by capture;
    it is picked up automatically once it exists.
    """
    parent = Path(database_path).expanduser().resolve().parent
    skills_home = resolve_planner_home(default=parent / "hermes-home")
    return {
        "files": parent / "files",
        "worker-settings": parent / "worker-settings",
        "skills": skills_home / "skills",
    }


def create_database_backup(
    source_db: Path,
    backup_dir: Path,
    deployed_revision: str,
) -> Path:
    """Create and publish one verified snapshot of the database and file tree."""
    source_db = source_db.expanduser().resolve()
    backup_dir = backup_dir.expanduser().resolve()
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    backup_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=".backup-", dir=backup_dir))
    try:
        temporary_database = temporary_dir / SNAPSHOT_DATABASE_NAME
        with (
            sqlite3.connect(source_db) as source_connection,
            sqlite3.connect(temporary_database) as destination_connection,
        ):
            source_connection.backup(destination_connection)
        _verify_database(temporary_database)
        checksum = _sha256(temporary_database)
        metadata: dict[str, object] = {
            "format": SNAPSHOT_FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "deployed_revision": deployed_revision,
            "sha256": checksum,
            "verified": True,
        }
        managed = _capture_managed_files(source_db, temporary_dir / SNAPSHOT_FILES_DIR_NAME)
        if managed is not None:
            # Verify the captured tree the same way the database is verified,
            # so a snapshot publishes only when both parts hold.
            if not _managed_files_valid(temporary_dir / SNAPSHOT_FILES_DIR_NAME, managed):
                raise RuntimeError("managed file capture failed verification")
            metadata["managed_files"] = managed
        (temporary_dir / SNAPSHOT_METADATA_NAME).write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n"
        )
        snapshot_name = (
            "snapshot-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        )
        snapshot = backup_dir / snapshot_name
        os.replace(temporary_dir, snapshot)
        apply_verified_snapshot_retention(backup_dir, verified_snapshot_retention_plan(backup_dir))
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
    """Restore one verified snapshot into a stopped live environment.

    Everything is validated before any destination is touched.  The database is
    restored first (atomic, as before), then each captured managed-file root is
    restored to its live location; the managed roots fully roll back among
    themselves if any one fails.
    """
    if not live_stopped:
        raise ValueError("live environment must be stopped before restore")
    snapshot = snapshot.expanduser()
    if snapshot.is_symlink() or not is_verified_snapshot(snapshot):
        raise ValueError("snapshot is not verified")
    snapshot = snapshot.resolve()
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
    managed = metadata.get("managed_files")
    if managed is not None:
        if not isinstance(managed, dict) or not _managed_files_valid(
            snapshot / SNAPSHOT_FILES_DIR_NAME, managed
        ):
            raise ValueError("snapshot is not verified")
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
    if managed is not None:
        _restore_managed_files(snapshot / SNAPSHOT_FILES_DIR_NAME, managed, destination_db)


def _verify_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed for {database}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_manifest(root: Path) -> dict[str, str]:
    """Map every regular file under ``root`` to its sha256, keyed by posix path."""
    manifest: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        manifest[path.relative_to(root).as_posix()] = _sha256(path)
    return manifest


def _capture_managed_files(source_db: Path, files_destination: Path) -> dict[str, object] | None:
    """Copy each existing managed-file root into the snapshot and record a manifest.

    Returns the metadata block describing the capture, or ``None`` when no managed
    root exists yet (an empty capture is not a failure).
    """
    roots = managed_file_roots(source_db)
    present = {name: path for name, path in roots.items() if path.exists()}
    if not present:
        return None
    files_destination.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}
    for name, path in present.items():
        target = files_destination / name
        # Preserve symlinks (skills are provisioned as symlinks to the packaged
        # source) rather than dereferencing them: a dangling link must not abort
        # the whole backup, and restore must reproduce the symlink structure.
        shutil.copytree(path, target, symlinks=True)
        manifest[name] = _relative_manifest(target)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (files_destination / SNAPSHOT_FILES_MANIFEST_NAME).write_bytes(manifest_bytes)
    return {
        "roots": sorted(manifest.keys()),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def _managed_files_valid(files_dir: Path, recorded: object) -> bool:
    """True when the captured tree still matches its recorded manifest exactly."""
    if not isinstance(recorded, dict):
        return False
    roots = recorded.get("roots")
    manifest_sha = recorded.get("manifest_sha256")
    if not isinstance(roots, list) or not isinstance(manifest_sha, str):
        return False
    manifest_path = files_dir / SNAPSHOT_FILES_MANIFEST_NAME
    if files_dir.is_symlink() or not files_dir.is_dir() or manifest_path.is_symlink():
        return False
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError:
        return False
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha:
        return False
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError:
        return False
    if not isinstance(manifest, dict) or sorted(manifest.keys()) != sorted(roots):
        return False
    for name in roots:
        root_copy = files_dir / name
        if (
            root_copy.is_symlink()
            or not root_copy.is_dir()
            or _relative_manifest(root_copy) != manifest.get(name)
        ):
            return False
    return True


def _restore_managed_files(
    files_dir: Path, recorded: dict[str, object], destination_db: Path
) -> None:
    """Restore each captured managed-file root to its live location, atomically.

    Every root is swapped into place through a temporary directory and an atomic
    rename.  If any root fails, the roots already replaced are rolled back to
    their pre-restore state before the error is re-raised.
    """
    roots = managed_file_roots(destination_db)
    names = recorded["roots"]
    if not isinstance(names, list):
        return
    replaced: list[tuple[Path, Path | None]] = []
    try:
        for name in names:
            source = files_dir / name
            target = roots[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(f".{target.name}.restore-tmp-{uuid.uuid4().hex}")
            old: Path | None = None
            try:
                shutil.copytree(source, staged, symlinks=True)
                if target.exists() or target.is_symlink():
                    old = target.with_name(f".{target.name}.restore-old-{uuid.uuid4().hex}")
                    os.replace(target, old)
                os.replace(staged, target)
            except BaseException:
                # Undo a stage-aside that outlived a failed swap, then discard
                # this root's staged copy so no temporary directory is left behind.
                if old is not None and not (target.exists() or target.is_symlink()):
                    os.replace(old, target)
                shutil.rmtree(staged, ignore_errors=True)
                raise
            replaced.append((target, old))
        for _, old in replaced:
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        for target, old in reversed(replaced):
            shutil.rmtree(target, ignore_errors=True)
            if old is not None:
                os.replace(old, target)
        raise


def verified_snapshots(backup_dir: Path) -> list[Path]:
    """Return only complete, checksum-verified snapshots, newest first."""
    configured_root = backup_dir.expanduser()
    if configured_root.is_symlink() or not configured_root.is_dir():
        return []
    root = configured_root.resolve()
    snapshots: list[tuple[Path, str]] = []
    for candidate in configured_root.glob("snapshot-*"):
        if (
            candidate.is_symlink()
            or candidate.parent.resolve() != root
            or not _is_verified_snapshot(candidate)
        ):
            continue
        try:
            metadata = json.loads((candidate / SNAPSHOT_METADATA_NAME).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        created_at = metadata.get("created_at") if isinstance(metadata, dict) else None
        snapshots.append((candidate, created_at if isinstance(created_at, str) else ""))
    return [
        path
        for path, _ in sorted(snapshots, key=lambda item: (item[1], item[0].name), reverse=True)
    ]


def is_verified_snapshot(candidate: Path) -> bool:
    """Whether ``candidate`` is a complete verified Panels backup snapshot."""
    metadata_path = candidate / SNAPSHOT_METADATA_NAME
    database_path = candidate / SNAPSHOT_DATABASE_NAME
    if (
        candidate.is_symlink()
        or not candidate.is_dir()
        or metadata_path.is_symlink()
        or not metadata_path.is_file()
        or database_path.is_symlink()
        or not database_path.is_file()
    ):
        return False
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict) or (
        metadata.get("format") != SNAPSHOT_FORMAT
        or metadata.get("verified") is not True
        or metadata.get("sha256") != _sha256(database_path)
    ):
        return False
    managed = metadata.get("managed_files")
    if managed is not None and not _managed_files_valid(
        candidate / SNAPSHOT_FILES_DIR_NAME, managed
    ):
        return False
    return True


def verified_snapshot_retention_plan(backup_dir: Path) -> tuple[Path, ...]:
    """The verified paths that retention may remove, oldest-first.

    Invalid and ambiguous directories never enter this plan.  This is the one
    proof used by backup publication, status, and host-local cleanup.
    """
    return tuple(reversed(verified_snapshots(backup_dir)[RETENTION_COUNT:]))


def apply_verified_snapshot_retention(backup_dir: Path, paths: tuple[Path, ...]) -> None:
    """Remove the supplied, still-proven verified snapshots under ``backup_dir``."""
    configured_root = backup_dir.expanduser()
    if configured_root.is_symlink() or not configured_root.is_dir():
        raise ValueError("backup retention root is no longer a regular directory")
    root = configured_root.resolve()
    current_plan = {path.resolve() for path in verified_snapshot_retention_plan(configured_root)}
    for candidate in paths:
        resolved = candidate.expanduser().resolve()
        if (
            candidate.is_symlink()
            or resolved.parent != root
            or resolved not in current_plan
            or not is_verified_snapshot(resolved)
        ):
            raise ValueError("backup retention target is no longer verified")
        # This writer adds one snapshot at a time to a directory it normally keeps at
        # RETENTION_COUNT, so one successful removal restores the invariant without
        # copying a snapshot merely to delete it. If that removal fails, no older
        # recovery point has been touched.
        shutil.rmtree(resolved)


# Backwards-compatible private aliases stay inside this module while callers use
# the public proof functions above.
def _verified_snapshots(backup_dir: Path) -> list[Path]:
    return verified_snapshots(backup_dir)


def _is_verified_snapshot(candidate: Path) -> bool:
    return is_verified_snapshot(candidate)


def _retain_verified_snapshots(backup_dir: Path) -> None:
    excess = verified_snapshot_retention_plan(backup_dir)
    if not excess:
        return
    apply_verified_snapshot_retention(backup_dir, excess)
