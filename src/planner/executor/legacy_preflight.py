"""Read-only resident preflight for the executor's existing journal format.

The public root is PLAN_EXECUTOR_ROOT, not its spool subdirectory. Inspection
never creates a directory, opens for writing, takes a lock, or migrates state.
A successful result is a compatibility observation, not update authorization.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from planner.executor.contracts import QueuedEnvelope


def check_spool_compatibility(root: Path, expected_format: int) -> None:
    """Accept absent/empty or recognized compatible ``root/spool``; else ValueError.

    ``root`` is the stable persistent PLAN_EXECUTOR_ROOT shared across packages.
    This observation performs no writes and cannot replace a fresh, identity-bound
    quiesce-and-exit before update or rollback. Unknown existing state is preserved.
    Filesystem inspection can also raise OSError (including directory/read races or
    permission failures). Consumers must refuse on both ValueError and OSError;
    neither exception is a compatible/empty result. Malformed metadata raises ValueError.
    """
    root_mode = _mode_if_present(root)
    if root_mode is not None and not stat.S_ISDIR(root_mode):
        raise ValueError("executor root is not a regular directory")
    _check_spool_directory(root / "spool", expected_format)


def _check_spool_directory(
    spool: Path, expected_format: int, *, ignored_names: frozenset[str] = frozenset()
) -> None:
    """The constructor shares this exact interpretation for its spool directory."""
    if type(expected_format) is not int or expected_format <= 0:
        raise ValueError("expected spool format must be a positive integer")
    if expected_format != 1:
        raise ValueError("expected spool format is unsupported by this reader")
    spool_mode = _mode_if_present(spool)
    if spool_mode is None:
        return
    if not stat.S_ISDIR(spool_mode):
        raise ValueError("unknown existing spool path")
    paths = tuple(path for path in spool.iterdir() if path.name not in ignored_names)
    if not paths:
        return
    metadata = spool / "metadata.json"
    metadata_mode = _mode_if_present(metadata)
    if metadata_mode is None or not stat.S_ISREG(metadata_mode):
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
        path_mode = path.lstat().st_mode
        if stat.S_ISLNK(path_mode):
            raise ValueError("unknown symlink in spool")
        if path.name in ("metadata.json", "lock"):
            if not stat.S_ISREG(path_mode):
                raise ValueError("unknown spool metadata path")
        elif path.name == "entries":
            if not stat.S_ISDIR(path_mode):
                raise ValueError("unknown spool entries path")
            _check_entries(path, sender)
        elif path.name.startswith(".metadata-"):
            if not stat.S_ISREG(path_mode):
                raise ValueError("unknown interrupted metadata path")
        elif path.name.startswith((".incomplete-", ".acknowledged-")):
            if not stat.S_ISDIR(path_mode):
                raise ValueError("unknown interrupted journal path")
        else:
            raise ValueError("unknown existing spool content")
    entries_mode = _mode_if_present(spool / "entries")
    if entries_mode is None or not stat.S_ISDIR(entries_mode):
        raise ValueError("corrupt spool: entries directory missing")


def _mode_if_present(path: Path) -> int | None:
    # Path.exists/is_dir/is_file suppress all OSError on newer Python releases.
    # Only an actual absent path is a successful empty-state observation here.
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None


def _check_entries(entries: Path, sender: str) -> None:
    for entry in entries.iterdir():
        if not stat.S_ISDIR(entry.lstat().st_mode) or not _safe_id(entry.name):
            raise ValueError("unknown published spool entry")
        metadata = entry / "envelope.json"
        if not stat.S_ISREG(metadata.lstat().st_mode):
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
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("unknown published entry path")
