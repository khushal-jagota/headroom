"""Filesystem operations shared by managed owner wrappers."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

from planner.core.errors import ErrorCode, PlannerError


def owner_root(files_root: Path, owner_id: str, *, create: bool, label: str) -> Path | None:
    if files_root.is_symlink():
        raise PlannerError(ErrorCode.validation, f"unsafe {label} file path", {})
    item_root = files_root / owner_id
    if create:
        if item_root.is_symlink():
            raise PlannerError(ErrorCode.validation, f"unsafe {label} file path", {})
        item_root.mkdir(parents=True, exist_ok=True)
        if item_root.is_symlink():
            raise PlannerError(ErrorCode.validation, f"unsafe {label} file path", {})
    elif not item_root.is_dir() or item_root.is_symlink():
        return None
    return item_root.resolve()


def safe_target(root: Path, relative_path: str, label: str) -> Path:
    parsed = PurePosixPath(relative_path)
    if (
        not relative_path
        or parsed.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise PlannerError(ErrorCode.validation, f"unsafe {label} file path", {})
    target = root.joinpath(*parsed.parts)
    try:
        target.parent.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlannerError(ErrorCode.validation, f"unsafe {label} file path", {}) from exc
    return target


def reject_symlink_ancestors(root: Path, target: Path, label: str) -> None:
    current = target.parent
    while current != root:
        if current.is_symlink():
            raise PlannerError(ErrorCode.validation, f"unsafe {label} file path", {})
        current = current.parent


def write_text(root: Path, relative_path: str, content: str, label: str) -> None:
    target = safe_target(root, relative_path, label)
    reject_symlink_ancestors(root, target, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def delete(root: Path | None, relative_path: str, label: str) -> None:
    if root is None:
        raise PlannerError(ErrorCode.not_found, f"{label} file not found", {})
    target = safe_target(root, relative_path, label)
    reject_symlink_ancestors(root, target, label)
    if not target.is_file() or target.is_symlink():
        raise PlannerError(ErrorCode.not_found, f"{label} file not found", {})
    target.unlink()
