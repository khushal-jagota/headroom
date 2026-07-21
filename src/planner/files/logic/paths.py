"""Resolve managed files without trusting string prefixes."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from planner.files.contracts import TicketFile

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ENCODED_UNSAFE_RE = re.compile(r"%(?:25|2e|2f|5c)", re.IGNORECASE)


def ticket_files_root(db_path: str | Path) -> Path:
    return Path(db_path).parent / "files" / "tickets"


def resolve_ticket_file(db_path: str | Path, ticket_id: str, relative_path: str) -> TicketFile:
    ticket_id = str(ticket_id)
    relative_path = str(relative_path)
    _validate_safe_id(ticket_id, "ticket")
    _validate_relative_path(relative_path)
    target = _resolve_managed_file(ticket_files_root(db_path), ticket_id, relative_path)
    return TicketFile(ticket_id=ticket_id, relative_path=relative_path, absolute_path=target)


def _validate_safe_id(value: str, label: str) -> None:
    if not value or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe {label} id")


def _resolve_managed_file(root_path: Path, entity_id: str, relative_path: str) -> Path:
    try:
        if root_path.is_symlink():
            raise ValueError("managed file root is a symlink")
        root = root_path.resolve(strict=True)
        entity_path = root / entity_id
        if entity_path.is_symlink():
            raise ValueError("managed entity directory is a symlink")
        entity_root = entity_path.resolve(strict=True)
        target = (entity_root / PurePosixPath(relative_path)).resolve(strict=True)
    except OSError as exc:
        raise ValueError("managed file not found") from exc
    try:
        target.relative_to(entity_root)
        entity_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("managed file escapes managed root") from exc
    if not target.is_file():
        raise ValueError("managed file is not a regular file")
    return target


def _validate_relative_path(relative_path: str) -> None:
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        raise ValueError("unsafe managed file path")
    if _ENCODED_UNSAFE_RE.search(relative_path):
        raise ValueError("encoded managed file traversal or separator")
    for raw_part in relative_path.split("/"):
        if raw_part in ("", ".", ".."):
            raise ValueError("unsafe managed file segment")
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute():
        raise ValueError("absolute managed file path")
    for part in parsed.parts:
        if part in ("", ".", ".."):
            raise ValueError("unsafe managed file segment")
