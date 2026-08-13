"""Recoverable lifecycle operations for managed Sprint Item files."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.files.logic.paths import sprint_item_files_root


@dataclass(frozen=True)
class QuarantinedSprintItemFiles:
    original: Path
    quarantine: Path


def database_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise RuntimeError("Sprint Item file lifecycle needs a file-backed database")
    return str(row[2])


def quarantine_sprint_item_files(
    conn: sqlite3.Connection, sprint_item_id: str
) -> QuarantinedSprintItemFiles | None:
    root = sprint_item_files_root(database_path(conn))
    if root.is_symlink():
        raise RuntimeError("managed Sprint Item file root is a symlink")
    original = root / sprint_item_id
    if not original.exists() and not original.is_symlink():
        return None
    quarantine_root = root.parent / ".sprint-item-quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    quarantine = quarantine_root / f"{sprint_item_id}.{uuid4().hex}"
    original.rename(quarantine)
    return QuarantinedSprintItemFiles(original, quarantine)


def restore_quarantined_sprint_item_files(
    quarantined: QuarantinedSprintItemFiles | None,
) -> None:
    if quarantined is None:
        return
    quarantined.quarantine.rename(quarantined.original)


def purge_quarantined_sprint_item_files(
    quarantined: QuarantinedSprintItemFiles | None,
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
