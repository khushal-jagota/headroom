"""Read-only resident preflight for the executor's existing journal format.

The public root is PLAN_EXECUTOR_ROOT, not its spool subdirectory. Inspection
never creates a directory, opens for writing, takes a lock, or migrates state.
A successful result is a compatibility observation, not update authorization.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from planner.executor.configuration import SPOOL_FORMAT_VERSION
from planner.executor.contracts import QueuedEnvelope


def check_spool_compatibility(root: Path, expected_format: int) -> None:
    """Accept absent/empty or recognized compatible ``root/spool``; else ValueError.

    ``root`` is the stable persistent PLAN_EXECUTOR_ROOT shared across packages.
    This observation performs no writes and cannot replace a fresh, identity-bound
    quiesce-and-exit before update or rollback. Unknown existing state is preserved.
    """
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("executor root is not a regular directory")
    _check_spool_directory(root / "spool", expected_format)


def _check_spool_directory(spool: Path, expected_format: int) -> None:
    """The constructor shares this exact interpretation for its spool directory."""
    if type(expected_format) is not int or expected_format <= 0:
        raise ValueError("expected spool format must be a positive integer")
    if expected_format != SPOOL_FORMAT_VERSION:
        raise ValueError("expected spool format is unsupported by this reader")
    if spool.is_symlink() or (spool.exists() and not spool.is_dir()):
        raise ValueError("unknown existing spool path")
    if not spool.exists():
        return
    paths = tuple(spool.iterdir())
    if not paths:
        return
    metadata = spool / "metadata.json"
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError("unknown spool: versioned metadata is missing")
    try:
        data = json.loads(metadata.read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError("corrupt spool metadata") from error
    if not isinstance(data, dict) or "format" not in data:
        raise ValueError("unknown spool: format version is missing")
    if type(data["format"]) is not int or data["format"] != expected_format:
        raise ValueError("incompatible spool; preserved without mutation")
    if set(data) != {"format", "sender_id", "identities", "sequences"}:
        raise ValueError("unknown spool metadata fields")
    sender = data["sender_id"]
    if not isinstance(sender, str):
        raise ValueError("corrupt spool sender identity")
    try:
        UUID(sender)
    except ValueError as error:
        raise ValueError("corrupt spool sender identity") from error
    identities, sequences = data["identities"], data["sequences"]
    if not isinstance(identities, dict) or not isinstance(sequences, dict):
        raise ValueError("corrupt spool sequence metadata")
    for conversation_id, sequence in sequences.items():
        if not _safe_id(conversation_id) or type(sequence) is not int or sequence < 0:
            raise ValueError("corrupt spool sequence counter")
    for submission_id, identity in identities.items():
        if (
            not _safe_id(submission_id)
            or not isinstance(identity, list)
            or len(identity) != 3
            or not _safe_id(identity[0])
            or type(identity[1]) is not int
            or identity[1] < 1
            or not isinstance(identity[2], str)
            or not re.fullmatch(r"[0-9a-f]{64}", identity[2])
            or sequences.get(identity[0], -1) < identity[1]
        ):
            raise ValueError("corrupt spool submission identity")
    for path in paths:
        if path.is_symlink():
            raise ValueError("unknown symlink in spool")
        if path.name in ("metadata.json", "lock"):
            if not path.is_file():
                raise ValueError("unknown spool metadata path")
        elif path.name == "entries":
            if not path.is_dir():
                raise ValueError("unknown spool entries path")
            _check_entries(path, sender)
        elif path.name.startswith(".metadata-"):
            if not path.is_file():
                raise ValueError("unknown interrupted metadata path")
        elif path.name.startswith((".incomplete-", ".acknowledged-")):
            if not path.is_dir():
                raise ValueError("unknown interrupted journal path")
        else:
            raise ValueError("unknown existing spool content")
    if not (spool / "entries").is_dir():
        raise ValueError("corrupt spool: entries directory missing")


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None


def _check_entries(entries: Path, sender: str) -> None:
    for entry in entries.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not _safe_id(entry.name):
            raise ValueError("unknown published spool entry")
        metadata = entry / "envelope.json"
        if metadata.is_symlink() or not metadata.is_file():
            raise ValueError("corrupt published spool entry")
        try:
            envelope = TypeAdapter(QueuedEnvelope).validate_json(metadata.read_bytes(), strict=True)
        except (OSError, ValueError, ValidationError) as error:
            raise ValueError("corrupt published envelope") from error
        if envelope.sender_id != sender or envelope.submission_id != entry.name:
            raise ValueError("corrupt published envelope identity")
        names = {"envelope.json"} | {
            f"file-{index}" for index in range(len(envelope.file_dependencies))
        }
        if {path.name for path in entry.iterdir()} != names:
            raise ValueError("unknown published entry content")
        for path in entry.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError("unknown published entry path")
