"""Read-only inspection of the SQLite outbox. Inspection never recovers or migrates."""

from __future__ import annotations

import json
import re
import sqlite3
import stat
from functools import cache
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from planner.executor.configuration import SPOOL_FORMAT_VERSION
from planner.executor.contracts import QueuedEnvelope
from planner.executor.outbox_storage import DATABASE_NAME, SCHEMA, SCHEMA_VERSION


def _mode_if_present(path: Path) -> int | None:
    # Path.exists/is_dir/is_file suppress all OSError on newer Python releases.
    # Only an actual absent path is a successful empty-state observation here.
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None


def check_spool_compatibility(root: Path, expected_format: int) -> None:
    mode = _mode_if_present(root)
    if mode is not None and not stat.S_ISDIR(mode):
        raise ValueError("executor root is not a regular directory")
    _check_spool_directory(root / "spool", expected_format)


def _check_spool_directory(
    spool: Path, expected_format: int, *, allow_recovery: bool = False
) -> None:
    if type(expected_format) is not int or expected_format != SPOOL_FORMAT_VERSION:
        raise ValueError("expected spool format is unsupported by this reader")
    mode = _mode_if_present(spool)
    if mode is None:
        return
    if not stat.S_ISDIR(mode):
        raise ValueError("unknown existing spool path")
    paths = tuple(spool.iterdir())
    if not paths:
        return
    # A lock-only tree is an interrupted fresh bootstrap. No sender or work exists.
    if {path.name for path in paths} == {"lock"}:
        if not stat.S_ISREG(paths[0].lstat().st_mode):
            raise ValueError("unknown lock path")
        return
    metadata_mode = _mode_if_present(spool / "metadata.json")
    metadata: dict[str, object] | None = None
    if metadata_mode is not None:
        if not stat.S_ISREG(metadata_mode):
            raise ValueError("unknown spool metadata path")
        try:
            metadata = json.loads((spool / "metadata.json").read_bytes())
        except ValueError as error:
            raise ValueError("corrupt spool metadata") from error
        if not isinstance(metadata, dict) or set(metadata) != {"format", "sender_id"}:
            raise ValueError("unknown spool metadata fields")
        if type(metadata["format"]) is not int or metadata["format"] != expected_format:
            raise ValueError("incompatible spool; preserved without mutation")
    elif not allow_recovery:
        raise ValueError("unknown spool: versioned metadata is missing")
    database_mode = _mode_if_present(spool / DATABASE_NAME)
    if database_mode is None or not stat.S_ISREG(database_mode):
        raise ValueError("corrupt spool: SQLite database missing")
    for path in paths:
        mode = path.lstat().st_mode
        if path.name == "entries":
            if not stat.S_ISDIR(mode):
                raise ValueError("unknown spool entries path")
        elif path.name in {DATABASE_NAME, DATABASE_NAME + "-journal", "metadata.json", "lock"}:
            if not stat.S_ISREG(mode):
                raise ValueError("unknown spool metadata path")
        elif path.name.startswith(".metadata-"):
            if not stat.S_ISREG(mode):
                raise ValueError("unknown interrupted metadata path")
        elif path.name.startswith((".incomplete-", ".acknowledged-")):
            if not stat.S_ISDIR(mode):
                raise ValueError("unknown interrupted journal path")
        else:
            raise ValueError("unknown existing spool content")
    journal = spool / (DATABASE_NAME + "-journal")
    journal_mode = _mode_if_present(journal)
    if journal_mode is not None and journal.stat().st_size:
        if allow_recovery:
            # The lifetime-locked constructor alone authorizes SQLite rollback.
            return
        raise ValueError("SQLite recovery required; read-only preflight refuses hot journal")
    try:
        connection = sqlite3.connect(
            (spool / DATABASE_NAME).resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            validate_database(connection, spool, metadata)
            if metadata is None and any(
                connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in ("identities", "conversations", "envelopes")
            ):
                raise ValueError("unknown spool: versioned metadata is missing")
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise ValueError("corrupt or incompatible SQLite outbox") from error


@cache
def _expected_schema() -> tuple[tuple[str, str, str | None], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA)
        return tuple(
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    finally:
        connection.close()


def validate_database(
    connection: sqlite3.Connection, spool: Path, metadata: dict[str, object] | None
) -> str:
    if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
        raise ValueError("unsupported SQLite journal mode")
    if connection.execute("PRAGMA user_version").fetchone() != (SCHEMA_VERSION,):
        raise ValueError("incompatible SQLite outbox schema")
    actual_schema = tuple(
        connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    if actual_schema != _expected_schema():
        raise ValueError("incompatible SQLite outbox schema structure")
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("corrupt SQLite outbox")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("corrupt SQLite identity references")
    row = connection.execute(
        "SELECT format, sender_id FROM outbox_metadata WHERE singleton=1"
    ).fetchone()
    if row is None or row[0] != SPOOL_FORMAT_VERSION or not isinstance(row[1], str):
        raise ValueError("corrupt SQLite sender identity")
    sender: str = row[1]
    try:
        UUID(sender)
    except ValueError as error:
        raise ValueError("corrupt SQLite sender identity") from error
    if metadata is not None and metadata["sender_id"] != sender:
        raise ValueError("conflicting spool sender identity")
    if connection.execute(
        "SELECT 1 FROM identities i LEFT JOIN conversations c USING(conversation_id) "
        "WHERE c.sequence IS NULL OR i.sequence > c.sequence LIMIT 1"
    ).fetchone():
        raise ValueError("corrupt spool sequence counter")
    entries = spool / "entries"
    if not stat.S_ISDIR(entries.lstat().st_mode):
        raise ValueError("corrupt spool entries path")
    for entry in entries.iterdir():
        if not stat.S_ISDIR(entry.lstat().st_mode) or not _safe_id(entry.name):
            raise ValueError("unknown published spool entry")
        # Extra orphan directories can remain after a failed SQLite publication.
        for child in entry.iterdir():
            if not stat.S_ISREG(child.lstat().st_mode):
                raise ValueError("unknown published entry path")
            if child.name != "envelope.json" and not (
                child.name.startswith("file-") and child.name[5:].isdigit()
            ):
                raise ValueError("unknown published entry content")
    for conversation, sequence in connection.execute(
        "SELECT conversation_id,sequence FROM conversations"
    ):
        if not _safe_id(conversation) or type(sequence) is not int or sequence < 0:
            raise ValueError("corrupt spool sequence counter")
    for submission, conversation, sequence, digest in connection.execute(
        "SELECT submission_id,conversation_id,sequence,payload_sha256 FROM identities"
    ):
        if (
            not _safe_id(submission)
            or not _safe_id(conversation)
            or type(sequence) is not int
            or sequence < 1
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("corrupt spool permanent identity")
    for submission, conversation, sequence, encoded, digest in connection.execute(
        "SELECT e.submission_id,e.conversation_id,e.sequence,e.envelope_json,i.payload_sha256 "
        "FROM envelopes e JOIN identities i USING(submission_id) WHERE e.pending=1"
    ):
        envelope = TypeAdapter(QueuedEnvelope).validate_json(encoded, strict=True)
        if (
            envelope.sender_id,
            envelope.submission_id,
            envelope.conversation_id,
            envelope.sequence,
            envelope.payload_sha256,
        ) != (sender, submission, conversation, sequence, digest):
            raise ValueError("corrupt published envelope identity")
        folder = entries / submission
        if not stat.S_ISDIR(folder.lstat().st_mode):
            raise ValueError("missing immutable snapshots")
        names = {"envelope.json"} | {f"file-{i}" for i in range(len(envelope.file_dependencies))}
        if {path.name for path in folder.iterdir()} != names:
            raise ValueError("corrupt immutable snapshots")
        if (folder / "envelope.json").read_bytes() != encoded:
            raise ValueError("conflicting envelope diagnostic copy")
    return sender
