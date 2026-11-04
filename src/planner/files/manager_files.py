"""Managed Manager files share the existing private storage root."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from planner.core import authority
from planner.core.authority import require_above_or_self
from planner.core.contracts import Principal
from planner.files import managed_files
from planner.files.logic.listing import entries_as_json, fold_directory
from planner.files.logic.paths import manager_files_root

# Where a Manager's agent puts the artifacts it produces. A convention its callers share,
# not a boundary the routes enforce: the Manager's root is what confines a write.
ARTIFACTS_DIRECTORY = "artifacts"


def list_files(
    conn: sqlite3.Connection, principal: Principal, manager_id: str, db_path: str
) -> list[dict[str, object]]:
    """Every managed file this Manager keeps, by the path that reads it back."""
    _require_manager(conn, principal, manager_id)
    root = _item_root(db_path, manager_id, create=False)
    if root is None:
        return []
    return [
        {"path": path.relative_to(root).as_posix(), "modified_at": path.stat().st_mtime}
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]


def list_artifact_entries(
    conn: sqlite3.Connection, principal: Principal, manager_id: str, db_path: str
) -> list[dict[str, object]]:
    """What a reader would open in this Manager's artifacts, folded a folder at a time.

    `list_files` stays flat, because an agent looking for the file it wrote needs every
    path. A person does not: a linked site of two dozen files is one thing to open. Both
    read the same directory, and the paths here are the same paths that read a file back.
    """
    _require_manager(conn, principal, manager_id)
    root = _item_root(db_path, manager_id, create=False)
    if root is None:
        return []
    artifacts = root / ARTIFACTS_DIRECTORY
    return entries_as_json(fold_directory(artifacts, f"{ARTIFACTS_DIRECTORY}/"))


def write_file(
    conn: sqlite3.Connection,
    principal: Principal,
    manager_id: str,
    db_path: str,
    relative_path: str,
    content: str,
) -> dict[str, object]:
    _require_manager(conn, principal, manager_id)
    root = _item_root(db_path, manager_id, create=True)
    assert root is not None
    managed_files.write_text(root, relative_path, content, "Manager")
    return {
        "manager_id": manager_id,
        "path": relative_path,
        "url": f"/files/managers/{manager_id}/{relative_path}",
    }


def delete_file(
    conn: sqlite3.Connection,
    principal: Principal,
    manager_id: str,
    db_path: str,
    relative_path: str,
) -> dict[str, object]:
    _require_manager(conn, principal, manager_id)
    root = _item_root(db_path, manager_id, create=False)
    managed_files.delete(root, relative_path, "Manager")
    return {"ok": True, "manager_id": manager_id, "path": relative_path}


def _require_manager(conn: sqlite3.Connection, principal: Principal, manager_id: str) -> None:
    require_above_or_self(conn, principal, authority.manager(manager_id))


def _item_root(db_path: str, manager_id: str, *, create: bool) -> Path | None:
    return managed_files.owner_root(
        manager_files_root(db_path), manager_id, create=create, label="Manager"
    )
