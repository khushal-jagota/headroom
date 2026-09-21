"""Writing and removing the managed files an Outcome keeps.

``GET /files/sprint-items/{i}/{p}`` has always served these bytes. Writing and removing
them lived somewhere else entirely, under a supervisor prefix, in a path space that
counted from a subdirectory rather than from the Outcome's own root — so the string a
caller wrote was never the string it could read back. One address space now, counted from
the Outcome's root, with the same relative path on the way in and the way out.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path, PurePosixPath
from uuid import uuid4

from planner.core import authority
from planner.core.authority import require_above_or_self
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.files.logic.listing import entries_as_json, fold_directory
from planner.files.logic.paths import sprint_item_files_root

# Where an Outcome's agent puts the artifacts it produces. A convention its callers share,
# not a boundary the routes enforce: the Outcome's root is what confines a write.
ARTIFACTS_DIRECTORY = "artifacts"


def list_files(
    conn: sqlite3.Connection, principal: Principal, sprint_item_id: str, db_path: str
) -> list[dict[str, object]]:
    """Every managed file this Outcome keeps, by the path that reads it back."""
    _require_outcome(conn, principal, sprint_item_id)
    root = _item_root(db_path, sprint_item_id, create=False)
    if root is None:
        return []
    return [
        {"path": path.relative_to(root).as_posix(), "modified_at": path.stat().st_mtime}
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]


def list_artifact_entries(
    conn: sqlite3.Connection, principal: Principal, sprint_item_id: str, db_path: str
) -> list[dict[str, object]]:
    """What a reader would open in this Outcome's artifacts, folded a folder at a time.

    `list_files` stays flat, because an agent looking for the file it wrote needs every
    path. A person does not: a linked site of two dozen files is one thing to open. Both
    read the same directory, and the paths here are the same paths that read a file back.
    """
    _require_outcome(conn, principal, sprint_item_id)
    root = _item_root(db_path, sprint_item_id, create=False)
    if root is None:
        return []
    artifacts = root / ARTIFACTS_DIRECTORY
    return entries_as_json(fold_directory(artifacts, f"{ARTIFACTS_DIRECTORY}/"))


def write_file(
    conn: sqlite3.Connection,
    principal: Principal,
    sprint_item_id: str,
    db_path: str,
    relative_path: str,
    content: str,
) -> dict[str, object]:
    _require_outcome(conn, principal, sprint_item_id)
    root = _item_root(db_path, sprint_item_id, create=True)
    assert root is not None
    target = _safe_target(root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(root, target)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "sprint_item_id": sprint_item_id,
        "path": relative_path,
        "url": f"/files/sprint-items/{sprint_item_id}/{relative_path}",
    }


def delete_file(
    conn: sqlite3.Connection,
    principal: Principal,
    sprint_item_id: str,
    db_path: str,
    relative_path: str,
) -> dict[str, object]:
    _require_outcome(conn, principal, sprint_item_id)
    root = _item_root(db_path, sprint_item_id, create=False)
    if root is None:
        raise PlannerError(ErrorCode.not_found, "Sprint Item file not found", {})
    target = _safe_target(root, relative_path)
    _reject_symlink_ancestors(root, target)
    if not target.is_file() or target.is_symlink():
        raise PlannerError(ErrorCode.not_found, "Sprint Item file not found", {})
    target.unlink()
    return {"ok": True, "sprint_item_id": sprint_item_id, "path": relative_path}


def _require_outcome(conn: sqlite3.Connection, principal: Principal, sprint_item_id: str) -> None:
    require_above_or_self(conn, principal, authority.outcome(sprint_item_id))


def _item_root(db_path: str, sprint_item_id: str, *, create: bool) -> Path | None:
    files_root = sprint_item_files_root(db_path)
    if files_root.is_symlink():
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item file path", {})
    item_root = files_root / sprint_item_id
    if create:
        item_root.mkdir(parents=True, exist_ok=True)
        if item_root.is_symlink():
            raise PlannerError(ErrorCode.validation, "unsafe Sprint Item file path", {})
    elif not item_root.is_dir() or item_root.is_symlink():
        return None
    return item_root.resolve()


def _safe_target(root: Path, relative_path: str) -> Path:
    parsed = PurePosixPath(relative_path)
    if (
        not relative_path
        or parsed.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item file path", {})
    target = root.joinpath(*parsed.parts)
    try:
        target.parent.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item file path", {}) from exc
    return target


def _reject_symlink_ancestors(root: Path, target: Path) -> None:
    current = target.parent
    while current != root:
        if current.is_symlink():
            raise PlannerError(ErrorCode.validation, "unsafe Sprint Item file path", {})
        current = current.parent
