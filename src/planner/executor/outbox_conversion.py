"""Explicit stopped-process format-1 conversion. No updater or two-host cutover."""

from __future__ import annotations

import fcntl
import json
import os
import stat
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from planner.executor.contracts import QueuedEnvelope
from planner.executor.files import fsync_directory, fsync_file
from planner.executor.journal import FilesystemSpool, envelope_payload_digest
from planner.executor.legacy_preflight import _check_spool_directory as check_legacy
from planner.executor.legacy_preflight import _mode_if_present
from planner.executor.outbox_storage import (
    DATABASE_NAME,
    connect_database,
    initialize_database,
    publish_metadata,
    write_file,
)
from planner.executor.preflight import _check_spool_directory, validate_database

CONVERSION_DIRECTORY = ".conversion-format2"


def convert_legacy_spool(spool: Path) -> None:
    """Convert under the existing lock, preserving the source and all diagnostic bytes.

    The durable conversion directory is a refusal fence for an old reader, whose
    preflight rejects unknown paths. It remains as the audit copy of old metadata.
    An interrupted conversion retries from the unchanged format-1 source. Publication
    of format-2 metadata is last. Once published, conversion is an idempotent check.
    """
    if not stat.S_ISDIR(spool.lstat().st_mode):
        raise ValueError("legacy spool is not a directory")
    if not stat.S_ISREG((spool / "lock").lstat().st_mode):
        raise ValueError("legacy spool lock is not a regular file")
    with (spool / "lock").open("r+b") as process_lock:
        try:
            fcntl.flock(process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("stop the executor before offline spool conversion") from error
        metadata_path = spool / "metadata.json"
        if not stat.S_ISREG(metadata_path.lstat().st_mode):
            raise ValueError("legacy metadata is not a regular file")
        raw_metadata = metadata_path.read_bytes()
        metadata = json.loads(raw_metadata)
        if not isinstance(metadata, dict):
            raise ValueError("corrupt legacy metadata")
        if metadata.get("format") == 2:
            _check_spool_directory(spool, 2)
            return
        if type(metadata.get("format")) is not int or metadata["format"] != 1:
            raise ValueError("converter supports only format 1")
        conversion = spool / CONVERSION_DIRECTORY
        existing_database = spool / DATABASE_NAME
        # A v1 spool never contains a SQLite database. Accept one only when a
        # completed local backup proves an interrupted publication by this converter.
        database_mode = _mode_if_present(existing_database)
        journal_mode = _mode_if_present(spool / (DATABASE_NAME + "-journal"))
        if database_mode is not None or journal_mode is not None:
            if (
                not conversion.is_dir()
                or not (conversion / "metadata-v1.json").is_file()
                or (conversion / "metadata-v1.json").read_bytes() != raw_metadata
                or database_mode is None
                or not stat.S_ISREG(database_mode)
            ):
                raise ValueError("unknown database beside legacy spool; preserved without mutation")
            journal = spool / (DATABASE_NAME + "-journal")
            if _mode_if_present(journal) is not None:
                raise ValueError(
                    "unexpected published conversion journal; preserved without mutation"
                )
        ignored = frozenset({CONVERSION_DIRECTORY, DATABASE_NAME, DATABASE_NAME + "-journal"})
        check_legacy(spool, 1, ignored_names=ignored)
        identities = dict(metadata["identities"])
        sequences = dict(metadata["sequences"])
        envelopes: list[tuple[QueuedEnvelope, bytes]] = []
        for entry in (spool / "entries").iterdir():
            encoded = (entry / "envelope.json").read_bytes()
            envelope = TypeAdapter(QueuedEnvelope).validate_json(encoded, strict=True)
            digest = envelope_payload_digest(
                envelope.payload,
                envelope.file_dependencies,
                sender_id=envelope.sender_id,
                conversation_id=envelope.conversation_id,
                sequence=envelope.sequence,
                submission_id=envelope.submission_id,
                kind=envelope.kind,
            )
            if digest != envelope.payload_sha256:
                raise ValueError("corrupt legacy envelope digest")
            identity = [envelope.conversation_id, envelope.sequence, envelope.payload_sha256]
            if (
                envelope.submission_id in identities
                and identities[envelope.submission_id] != identity
            ):
                raise ValueError("legacy submission identity conflicts with entry")
            identities[envelope.submission_id] = identity
            sequences[envelope.conversation_id] = max(
                sequences.get(envelope.conversation_id, 0), envelope.sequence
            )
            for index, manifest in enumerate(envelope.file_dependencies):
                snapshot = entry / f"file-{index}"
                FilesystemSpool._verify_file(snapshot, manifest)
                with snapshot.open("rb") as stream:
                    fsync_file(stream.fileno())
            # Legacy writes did not flush the macOS drive cache. Establish the
            # format-2 file-before-reference guarantee on every conversion attempt.
            with (entry / "envelope.json").open("rb") as stream:
                fsync_file(stream.fileno())
            fsync_directory(entry)
            envelopes.append((envelope, encoded))
        fsync_directory(spool / "entries")
        if _mode_if_present(conversion) is not None:
            if not stat.S_ISDIR(conversion.lstat().st_mode):
                raise ValueError("unknown conversion fence")
            for path in conversion.iterdir():
                if path.name not in {
                    "metadata-v1.json",
                    DATABASE_NAME,
                    DATABASE_NAME + "-journal",
                } and not path.name.startswith(".backup-"):
                    raise ValueError("unknown offline conversion content")
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("unknown offline conversion path")
        else:
            conversion.mkdir(mode=0o700)
            fsync_directory(spool)
        backup = conversion / "metadata-v1.json"
        if backup.exists():
            if backup.read_bytes() != raw_metadata:
                raise ValueError("legacy metadata changed after offline conversion started")
        else:
            temporary_backup = conversion / f".backup-{uuid4().hex}"
            write_file(temporary_backup, raw_metadata)
            os.replace(temporary_backup, backup)
            fsync_directory(conversion)
        if database_mode is not None:
            # A prior publication must exactly match the unchanged source before
            # this converter can replace it. Unknown databases remain untouched.
            import sqlite3

            published = sqlite3.connect(existing_database.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if validate_database(published, spool, None) != metadata["sender_id"]:
                    raise ValueError("published conversion sender differs from legacy source")
                if (
                    dict(published.execute("SELECT conversation_id,sequence FROM conversations"))
                    != sequences
                    or {
                        row[0]: list(row[1:])
                        for row in published.execute("SELECT * FROM identities")
                    }
                    != identities
                    or {
                        row[0]: row[1]
                        for row in published.execute(
                            "SELECT submission_id,envelope_json FROM envelopes WHERE pending=1"
                        )
                    }
                    != {e.submission_id: encoded for e, encoded in envelopes}
                ):
                    raise ValueError("published conversion differs from legacy source")
            finally:
                published.close()
        # Only the unpublished private database can be discarded and rebuilt.
        private = conversion / DATABASE_NAME
        private.unlink(missing_ok=True)
        (conversion / (DATABASE_NAME + "-journal")).unlink(missing_ok=True)
        connection = connect_database(private)
        try:
            initialize_database(connection, metadata["sender_id"])
            with connection:
                connection.executemany("INSERT INTO conversations VALUES (?,?)", sequences.items())
                connection.executemany(
                    "INSERT INTO identities VALUES (?,?,?,?)",
                    ((submission, *identity) for submission, identity in identities.items()),
                )
                connection.executemany(
                    "INSERT INTO envelopes VALUES (?,?,?,?,1)",
                    (
                        (e.submission_id, e.conversation_id, e.sequence, encoded)
                        for e, encoded in envelopes
                    ),
                )
            validate_database(connection, spool, None)
        finally:
            connection.close()
        fsync_directory(conversion)
        os.replace(private, spool / DATABASE_NAME)
        fsync_directory(spool)
        publish_metadata(spool, metadata["sender_id"])
        _check_spool_directory(spool, 2)
