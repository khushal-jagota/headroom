"""Recoverable lifecycle operations for managed Manager files."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.files.logic.paths import manager_files_root


@dataclass(frozen=True)
class QuarantinedManagerFiles:
    original: Path
    quarantine: Path


def database_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise RuntimeError("Manager file lifecycle needs a file-backed database")
    return str(row[2])


def quarantine_manager_files(
    conn: sqlite3.Connection, manager_id: str
) -> QuarantinedManagerFiles | None:
    root = manager_files_root(database_path(conn))
    if root.is_symlink():
        raise RuntimeError("managed Manager file root is a symlink")
    original = root / manager_id
    if not original.exists() and not original.is_symlink():
        return None
    quarantine_root = root.parent / ".sprint-item-quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    quarantine = quarantine_root / f"{manager_id}.{uuid4().hex}"
    original.rename(quarantine)
    return QuarantinedManagerFiles(original, quarantine)


def restore_quarantined_manager_files(
    quarantined: QuarantinedManagerFiles | None,
) -> None:
    if quarantined is None:
        return
    quarantined.quarantine.rename(quarantined.original)


def purge_quarantined_manager_files(
    quarantined: QuarantinedManagerFiles | None,
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
