"""Resolve managed files without trusting string prefixes."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from planner.files.contracts import SprintItemFile, TicketFile

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ENCODED_UNSAFE_RE = re.compile(r"%(?:25|2e|2f|5c)", re.IGNORECASE)


def ticket_files_root(db_path: str | Path) -> Path:
    return Path(db_path).parent / "files" / "tickets"


def conversation_files_root(db_path: str | Path) -> Path:
    """Where the files a conversation's messages carry are kept.

    A second family of managed files under the same root as ticket files, resolved by the
    same rules. A conversation's own folder is named by its id, so a message can only ever
    reach a file kept for the conversation it belongs to.
    """
    return Path(db_path).parent / "files" / "conversations"


def sprint_item_files_root(db_path: str | Path) -> Path:
    return Path(db_path).parent / "files" / "sprint-items"


def resolve_ticket_file(db_path: str | Path, ticket_id: str, relative_path: str) -> TicketFile:
    ticket_id = str(ticket_id)
    relative_path = str(relative_path)
    _validate_safe_id(ticket_id, "ticket")
    _validate_relative_path(relative_path)
    target = _resolve_managed_file(ticket_files_root(db_path), ticket_id, relative_path)
    return TicketFile(ticket_id=ticket_id, relative_path=relative_path, absolute_path=target)


def resolve_sprint_item_file(
    db_path: str | Path, sprint_item_id: str, relative_path: str
) -> SprintItemFile:
    sprint_item_id = str(sprint_item_id)
    relative_path = str(relative_path)
    _validate_safe_id(sprint_item_id, "Sprint Item")
    _validate_relative_path(relative_path)
    target = _resolve_managed_file(sprint_item_files_root(db_path), sprint_item_id, relative_path)
    return SprintItemFile(sprint_item_id, relative_path, target)


def resolve_conversation_file(
    db_path: str | Path, conversation_id: str, stored_file_id: str
) -> Path:
    """Where the bytes of one file a conversation kept actually are.

    Both names are ids rather than paths — the conversation's and the file's — so there is
    no relative path to walk and nothing a caller could point outside the root. They are
    still resolved through the same check as any other managed file, because the defence
    that matters is the one that runs even when the input looked safe.
    """
    conversation_id = str(conversation_id)
    stored_file_id = str(stored_file_id)
    _validate_safe_id(conversation_id, "conversation")
    _validate_safe_id(stored_file_id, "stored file")
    return _resolve_managed_file(conversation_files_root(db_path), conversation_id, stored_file_id)


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
