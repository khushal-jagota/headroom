"""Resolve managed ticket files without trusting string prefixes."""

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
    _validate_ticket_id(ticket_id)
    _validate_relative_path(relative_path)

    try:
        root = ticket_files_root(db_path).resolve(strict=True)
        ticket_root = (root / ticket_id).resolve(strict=True)
        target = (ticket_root / PurePosixPath(relative_path)).resolve(strict=True)
    except OSError as exc:
        raise ValueError("ticket file not found") from exc
    try:
        target.relative_to(ticket_root)
        ticket_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("ticket file escapes managed root") from exc
    if not target.is_file():
        raise ValueError("ticket file is not a regular file")
    return TicketFile(ticket_id=ticket_id, relative_path=relative_path, absolute_path=target)


def _validate_ticket_id(ticket_id: str) -> None:
    if not ticket_id or not _SAFE_ID_RE.fullmatch(ticket_id):
        raise ValueError("unsafe ticket id")


def _validate_relative_path(relative_path: str) -> None:
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        raise ValueError("unsafe ticket file path")
    if _ENCODED_UNSAFE_RE.search(relative_path):
        raise ValueError("encoded ticket file traversal or separator")
    for raw_part in relative_path.split("/"):
        if raw_part in ("", ".", ".."):
            raise ValueError("unsafe ticket file segment")
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute():
        raise ValueError("absolute ticket file path")
    for part in parsed.parts:
        if part in ("", ".", ".."):
            raise ValueError("unsafe ticket file segment")
