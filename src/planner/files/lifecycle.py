"""Recoverable lifecycle operations for managed files."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.files.logic.paths import goal_files_root, manager_files_root


@dataclass(frozen=True)
class QuarantinedFiles:
    original: Path
    quarantine: Path


def database_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise RuntimeError("managed file lifecycle needs a file-backed database")
    return str(row[2])


def quarantine_manager_files(conn: sqlite3.Connection, manager_id: str) -> QuarantinedFiles | None:
    return _quarantine_files(
        manager_files_root(database_path(conn)), manager_id, ".sprint-item-quarantine", "Manager"
    )


def quarantine_goal_files(conn: sqlite3.Connection, goal_id: str) -> QuarantinedFiles | None:
    return _quarantine_files(
        goal_files_root(database_path(conn)), goal_id, ".goal-quarantine", "goal"
    )


def _quarantine_files(
    root: Path, owner_id: str, quarantine_directory: str, label: str
) -> QuarantinedFiles | None:
    if root.is_symlink():
        raise RuntimeError(f"managed {label} file root is a symlink")
    original = root / owner_id
    if not original.exists() and not original.is_symlink():
        return None
    quarantine_root = root.parent / quarantine_directory
    quarantine_root.mkdir(parents=True, exist_ok=True)
    quarantine = quarantine_root / f"{owner_id}.{uuid4().hex}"
    original.rename(quarantine)
    return QuarantinedFiles(original, quarantine)


def restore_quarantined_files(
    quarantined: QuarantinedFiles | None,
) -> None:
    if quarantined is None:
        return
    quarantined.quarantine.rename(quarantined.original)


def purge_quarantined_files(
    quarantined: QuarantinedFiles | None,
) -> None:
    if quarantined is None:
        return
    try:
        if quarantined.quarantine.is_symlink() or quarantined.quarantine.is_file():
            quarantined.quarantine.unlink()
        else:
            shutil.rmtree(quarantined.quarantine)
    except OSError:
        # The database deletion already committed. The quarantined path is unreachable
        # through the managed-file route and a later maintenance pass can remove it.
        return
