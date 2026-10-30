"""SQLite schema and durable local publication for the Mac outbox."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from planner.executor.configuration import OUTBOX_BUSY_TIMEOUT_MS, SPOOL_FORMAT_VERSION
from planner.executor.files import fsync_directory, fsync_file

DATABASE_NAME = "outbox.sqlite3"
SCHEMA_VERSION = 3
SCHEMA = """
CREATE TABLE outbox_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    format INTEGER NOT NULL CHECK(format = 3),
    sender_id TEXT NOT NULL
);
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL CHECK(sequence >= 0)
) WITHOUT ROWID;
CREATE TABLE identities (
    submission_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    payload_sha256 TEXT NOT NULL,
    UNIQUE(conversation_id, sequence)
) WITHOUT ROWID;
CREATE TABLE envelopes (
    submission_id TEXT PRIMARY KEY REFERENCES identities(submission_id),
    conversation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    envelope_json BLOB NOT NULL,
    pending INTEGER NOT NULL CHECK(pending IN (0, 1)),
    FOREIGN KEY(conversation_id, sequence) REFERENCES identities(conversation_id, sequence)
) WITHOUT ROWID;
CREATE INDEX pending_conversation_sequence ON envelopes(conversation_id, sequence)
    WHERE pending = 1;
"""


def connect_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path, timeout=OUTBOX_BUSY_TIMEOUT_MS / 1000, check_same_thread=False
    )
    try:
        connection.execute(f"PRAGMA busy_timeout={OUTBOX_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=EXTRA")
        connection.execute("PRAGMA fullfsync=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection
    except BaseException:
        connection.close()
        raise


def initialize_database(connection: sqlite3.Connection, sender_id: str) -> None:
    # DDL and identity publish together. A partial empty database never means a new sender.
    connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
    try:
        connection.execute(
            "INSERT INTO outbox_metadata VALUES (1, ?, ?)", (SPOOL_FORMAT_VERSION, sender_id)
        )
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def write_file(path: Path, contents: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(contents)
        stream.flush()
        fsync_file(stream.fileno())


def publish_metadata(root: Path, sender_id: str) -> None:
    temporary = root / f".metadata-{uuid4().hex}"
    write_file(
        temporary, json.dumps({"format": SPOOL_FORMAT_VERSION, "sender_id": sender_id}).encode()
    )
    os.replace(temporary, root / "metadata.json")
    fsync_directory(root)


def durable_directory(path: Path) -> None:
    """Publish each new ancestor before its child, including an interrupted anchor."""
    missing: list[Path] = []
    anchor = path
    while not anchor.exists():
        missing.append(anchor)
        anchor = anchor.parent
    fsync_directory(anchor.parent)
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        fsync_directory(directory.parent)
    fsync_directory(path.parent)
