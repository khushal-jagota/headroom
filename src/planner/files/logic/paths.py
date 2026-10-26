"""Resolve managed files without trusting string prefixes."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from planner.files.contracts import ManagerFile, TicketFile

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


def manager_files_root(db_path: str | Path) -> Path:
    # Retain the existing private directory so both URL generations resolve the same bytes.
    return Path(db_path).parent / "files" / "sprint-items"


def resolve_ticket_file(db_path: str | Path, ticket_id: str, relative_path: str) -> TicketFile:
    ticket_id = str(ticket_id)
    relative_path = str(relative_path)
    _validate_safe_id(ticket_id, "ticket")
    _validate_relative_path(relative_path)
    target = _resolve_managed_file(ticket_files_root(db_path), ticket_id, relative_path)
    return TicketFile(ticket_id=ticket_id, relative_path=relative_path, absolute_path=target)


def resolve_manager_file(
    db_path: str | Path, manager_id: str, relative_path: str
) -> ManagerFile:
    manager_id = str(manager_id)
    relative_path = str(relative_path)
    _validate_safe_id(manager_id, "Manager")
    _validate_relative_path(relative_path)
    target = _resolve_managed_file(manager_files_root(db_path), manager_id, relative_path)
    return ManagerFile(manager_id, relative_path, target)


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


def html_navigation_owner(href: str) -> tuple[str, str, str]:
    """Decode a managed URL once; reject ambiguous URL and path syntax."""
    if any(ord(char) < 32 for char in href) or "\\" in href:
        raise ValueError("unsafe managed URL")
    url = urlsplit(href)
    if url.scheme or url.netloc or url.query or url.fragment or not href.startswith("/files/"):
        raise ValueError("unsafe managed URL")
    if _ENCODED_UNSAFE_RE.search(url.path):
        raise ValueError("encoded managed traversal or separator")
    path = unquote(url.path, errors="strict")
    parts = path.split("/", 4)
    if len(parts) != 5 or parts[2] not in ("tickets", "managers", "sprint-items"):
        raise ValueError("unsafe managed URL")
    kind = "tickets" if parts[2] == "tickets" else "managers"
    _validate_safe_id(parts[3], "managed owner")
    _validate_relative_path(parts[4])
    if "\x00" in parts[4] or Path(parts[4]).suffix.lower() not in (".html", ".htm"):
        raise ValueError("not an HTML artifact")
    return kind, parts[3], parts[4]


def resolve_html_navigation(db_path: str | Path, root_href: str, candidate_href: str) -> Path:
    """Confine a read to the original artifact directory, including real symlink targets.

    The root directory is resolved once. Both lexical and resolved candidate paths
    must remain inside it. The ordinary file routes keep their delivery policy.
    This read-only check does not lock out concurrent filesystem writers.
    """
    kind, owner, root_relative = html_navigation_owner(root_href)
    candidate_kind, candidate_owner, candidate_relative = html_navigation_owner(candidate_href)
    if (kind, owner) != (candidate_kind, candidate_owner):
        raise ValueError("different managed owner")
    logical_root = PurePosixPath(root_relative).parent
    PurePosixPath(candidate_relative).relative_to(logical_root)
    resolver = resolve_ticket_file if kind == "tickets" else resolve_manager_file
    # Resolve the lexical parent itself, not the resolved root document's parent:
    # a root document can itself be a symlink to another directory.
    owner_root = (ticket_files_root(db_path) if kind == "tickets" else manager_files_root(db_path))
    artifact_root = (owner_root / owner / logical_root).resolve(strict=True)
    root_file = resolver(db_path, owner, root_relative)
    root_file.absolute_path.relative_to(artifact_root)
    candidate = resolver(db_path, owner, candidate_relative)
    candidate.absolute_path.relative_to(artifact_root)
    return candidate.absolute_path
