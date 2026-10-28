"""SQLite durable outbox with immutable, independently pinned file snapshots."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from planner.executor.configuration import OUTBOX_COPY_BYTES, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileManifest,
    FileSnapshot,
    JournalPublication,
    JsonObject,
    QueuedEnvelope,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import fsync_directory, fsync_file, safe_component
from planner.executor.outbox_storage import (
    DATABASE_NAME,
    connect_database,
    durable_directory,
    initialize_database,
    publish_metadata,
    write_file,
)
from planner.executor.preflight import _check_spool_directory, validate_database
from planner.executor.wire import encode_frame


def envelope_payload_digest(
    payload: JsonObject,
    manifests: tuple[FileManifest, ...],
    *,
    sender_id: str,
    conversation_id: str,
    sequence: int,
    submission_id: str,
    kind: EnvelopeKind,
) -> str:
    value = {
        "payload": payload,
        "sender_id": sender_id,
        "conversation_id": conversation_id,
        "sequence": sequence,
        "submission_id": submission_id,
        "kind": kind.value,
        "file_dependencies": TypeAdapter(tuple[FileManifest, ...]).dump_python(
            manifests, mode="json"
        ),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class FilesystemSpool:
    """One process owns a spool. Its threads share one serialized SQLite connection."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._pins: dict[str, int] = {}
        self._closed = False
        self._database: sqlite3.Connection | None = None
        # This validates metadata and names, but permits authorized rollback recovery.
        _check_spool_directory(root, SPOOL_FORMAT_VERSION, allow_recovery=True)
        durable_directory(root)
        self._process_lock = (root / "lock").open("a+b")
        try:
            fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _check_spool_directory(root, SPOOL_FORMAT_VERSION, allow_recovery=True)
            self._entries = root / "entries"
            self._entries.mkdir(exist_ok=True, mode=0o700)
            database_path = root / DATABASE_NAME
            existed = database_path.exists()
            self._database = connect_database(database_path)
            if not existed:
                self._sender_id = uuid4().hex
                initialize_database(self._database, self._sender_id)
                fsync_directory(root)
            else:
                # Opening under flock authorizes SQLite to undo an interrupted transaction.
                self._sender_id = validate_database(self._database, root, None)
            if not (root / "metadata.json").exists():
                publish_metadata(root, self._sender_id)
            _check_spool_directory(root, SPOOL_FORMAT_VERSION)
            self._cleanup_acknowledged()
        except BaseException:
            self.close()
            raise

    @property
    def _db(self) -> sqlite3.Connection:
        if self._closed or self._database is None:
            raise RuntimeError("outbox is closed")
        return self._database

    @property
    def sender_id(self) -> str:
        return self._sender_id

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._pins:
                raise RuntimeError("cannot close outbox with pinned snapshots")
            self._closed = True
            if self._database is not None:
                self._database.close()
            self._process_lock.close()

    def mint_envelope(
        self,
        *,
        conversation_id: str,
        kind: EnvelopeKind,
        payload: JsonObject,
        snapshots: tuple[FileSnapshot, ...] = (),
        submission_id: str | None = None,
    ) -> QueuedEnvelope:
        with self._lock:
            safe_component(conversation_id)
            identity = safe_component(submission_id or uuid4().hex)
            manifests = tuple(snapshot.manifest for snapshot in snapshots)
            previous = self._db.execute(
                "SELECT conversation_id, sequence, payload_sha256 FROM identities "
                "WHERE submission_id=?",
                (identity,),
            ).fetchone()
            counter = self._db.execute(
                "SELECT sequence FROM conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            sequence = previous[1] if previous else (counter[0] if counter else 0) + 1
            digest = envelope_payload_digest(
                payload,
                manifests,
                sender_id=self.sender_id,
                conversation_id=conversation_id,
                sequence=sequence,
                submission_id=identity,
                kind=kind,
            )
            if previous is not None and previous != (conversation_id, sequence, digest):
                raise ValueError("submission id reused with different content")
            envelope = QueuedEnvelope(
                self.sender_id,
                conversation_id,
                sequence,
                identity,
                kind,
                payload,
                digest,
                manifests,
            )
            self.enqueue(envelope, snapshots)
            return envelope

    def enqueue(self, envelope: QueuedEnvelope, snapshots: tuple[FileSnapshot, ...] = ()) -> None:
        with self._lock:
            encode_frame(
                WireMessage(
                    WireMessageKind.envelope,
                    "0" * 32,
                    TypeAdapter(QueuedEnvelope).dump_python(envelope, mode="json"),
                )
            )
            safe_component(envelope.submission_id)
            safe_component(envelope.conversation_id)
            if envelope.sender_id != self.sender_id:
                raise ValueError("foreign sender")
            if (
                envelope.sequence < 1
                or envelope_payload_digest(
                    envelope.payload,
                    envelope.file_dependencies,
                    sender_id=envelope.sender_id,
                    conversation_id=envelope.conversation_id,
                    sequence=envelope.sequence,
                    submission_id=envelope.submission_id,
                    kind=envelope.kind,
                )
                != envelope.payload_sha256
            ):
                raise ValueError("invalid envelope digest")
            previous = self._db.execute(
                "SELECT conversation_id,sequence,payload_sha256 FROM identities "
                "WHERE submission_id=?",
                (envelope.submission_id,),
            ).fetchone()
            identity = (envelope.conversation_id, envelope.sequence, envelope.payload_sha256)
            if previous is not None and previous != identity:
                raise ValueError("submission identity conflicts with journal")
            existing = self._db.execute(
                "SELECT envelope_json,pending FROM envelopes WHERE submission_id=?",
                (envelope.submission_id,),
            ).fetchone()
            encoded = TypeAdapter(QueuedEnvelope).dump_json(envelope)
            if existing is not None:
                if TypeAdapter(QueuedEnvelope).validate_json(existing[0]) != envelope:
                    raise ValueError("conflicting immutable entry")
                encoded = existing[0]
                if existing[1]:
                    return
            if tuple(snapshot.manifest for snapshot in snapshots) != envelope.file_dependencies:
                raise ValueError("file dependencies lack snapshots")
            if existing is not None:
                # ACK cleanup can fail after removing some immutable files. Finish it
                # before republishing a retry, unless a reader still pins the full entry.
                self._cleanup_submission(envelope.submission_id)
            encoded = self._publish_snapshots(envelope, snapshots, encoded)
            try:
                with self._db:
                    self._db.execute(
                        "INSERT INTO conversations VALUES (?,?) ON CONFLICT(conversation_id) "
                        "DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                        (envelope.conversation_id, envelope.sequence),
                    )
                    self._db.execute(
                        "INSERT INTO identities VALUES (?,?,?,?) "
                        "ON CONFLICT(submission_id) DO NOTHING",
                        (envelope.submission_id, *identity),
                    )
                    self._db.execute(
                        "INSERT INTO envelopes VALUES (?,?,?,?,1) ON CONFLICT(submission_id) "
                        "DO UPDATE SET pending=1",
                        (
                            envelope.submission_id,
                            envelope.conversation_id,
                            envelope.sequence,
                            encoded,
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise ValueError("submission sequence conflicts with journal") from error
            for snapshot in snapshots:
                if snapshot.local_path.parent.name == ".executor-snapshots":
                    snapshot.local_path.unlink(missing_ok=True)

    def _publish_snapshots(
        self, envelope: QueuedEnvelope, snapshots: tuple[FileSnapshot, ...], encoded: bytes
    ) -> bytes:
        target = self._entries / envelope.submission_id
        if target.exists():
            original = (target / "envelope.json").read_bytes()
            if TypeAdapter(QueuedEnvelope).validate_json(original) != envelope:
                raise ValueError("conflicting immutable entry")
            # A previous DB publication failed, or an ACK still has a pinned reader.
            # Reuse precisely the durable bytes, never overwrite an in-flight snapshot.
            for index, manifest in enumerate(envelope.file_dependencies):
                self._verify_file(target / f"file-{index}", manifest)
            fsync_directory(target)
            fsync_directory(self._entries)
            return original
        temporary = self.root / f".incomplete-{uuid4().hex}"
        temporary.mkdir(mode=0o700)
        for index, snapshot in enumerate(snapshots):
            digest = hashlib.sha256()
            count = 0
            with (
                snapshot.local_path.open("rb") as source,
                (temporary / f"file-{index}").open("xb") as out,
            ):
                while block := source.read(OUTBOX_COPY_BYTES):
                    count += len(block)
                    digest.update(block)
                    out.write(block)
                out.flush()
                fsync_file(out.fileno())
            if (
                count != snapshot.manifest.byte_count
                or digest.hexdigest() != snapshot.manifest.sha256
            ):
                raise ValueError("snapshot changed before durable publication")
        write_file(temporary / "envelope.json", encoded)
        fsync_directory(temporary)
        os.rename(temporary, target)
        fsync_directory(self._entries)
        return encoded

    @staticmethod
    def _verify_file(path: Path, manifest: FileManifest) -> None:
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            while block := stream.read(OUTBOX_COPY_BYTES):
                count += len(block)
                digest.update(block)
        if count != manifest.byte_count or digest.hexdigest() != manifest.sha256:
            raise ValueError("corrupt immutable snapshot")

    def lookup_publication(self, submission_id: str) -> JournalPublication | None:
        """Inspect retained identity without recapture, requeue, or sequence allocation."""
        with self._lock:
            row = self._db.execute(
                "SELECT envelope_json,pending FROM envelopes WHERE submission_id=?",
                (submission_id,),
            ).fetchone()
            if row is None:
                return None
            return JournalPublication(
                TypeAdapter(QueuedEnvelope).validate_json(row[0]), bool(row[1])
            )

    def pending(self) -> tuple[QueuedEnvelope, ...]:
        with self._lock:
            return tuple(
                TypeAdapter(QueuedEnvelope).validate_json(row[0])
                for row in self._db.execute(
                    "SELECT envelope_json FROM envelopes WHERE pending=1 "
                    "ORDER BY conversation_id,sequence"
                )
            )

    def pending_heads(self) -> tuple[QueuedEnvelope, ...]:
        with self._lock:
            return tuple(
                TypeAdapter(QueuedEnvelope).validate_json(row[0])
                for row in self._db.execute(
                    "SELECT e.envelope_json FROM envelopes e JOIN "
                    "(SELECT conversation_id, MIN(sequence) AS sequence FROM envelopes "
                    "WHERE pending=1 GROUP BY conversation_id) h "
                    "ON e.conversation_id=h.conversation_id AND e.sequence=h.sequence "
                    "WHERE e.pending=1 ORDER BY e.conversation_id"
                )
            )

    def pending_sequence_ceilings(self) -> dict[str, int]:
        with self._lock:
            return dict(
                self._db.execute(
                    "SELECT conversation_id, MAX(sequence) FROM envelopes "
                    "WHERE pending=1 GROUP BY conversation_id"
                )
            )

    def pending_count(self) -> int:
        with self._lock:
            return int(
                self._db.execute("SELECT count(*) FROM envelopes WHERE pending=1").fetchone()[0]
            )

    def has_pending(self, conversation_id: str | None = None) -> bool:
        with self._lock:
            query = "SELECT 1 FROM envelopes WHERE pending=1"
            parameters = () if conversation_id is None else (conversation_id,)
            if conversation_id is not None:
                query += " AND conversation_id=?"
            return self._db.execute(query + " LIMIT 1", parameters).fetchone() is not None

    def snapshots(self, submission_id: str) -> tuple[FileSnapshot, ...]:
        with self._lock:
            row = self._db.execute(
                "SELECT envelope_json FROM envelopes WHERE submission_id=? AND pending=1",
                (safe_component(submission_id),),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(submission_id)
            envelope = TypeAdapter(QueuedEnvelope).validate_json(row[0])
            return tuple(
                FileSnapshot(manifest, self._entries / submission_id / f"file-{index}")
                for index, manifest in enumerate(envelope.file_dependencies)
            )

    @contextmanager
    def pin_snapshots(self, submission_id: str) -> Iterator[tuple[FileSnapshot, ...]]:
        with self._lock:
            snapshots = self.snapshots(submission_id)
            self._pins[submission_id] = self._pins.get(submission_id, 0) + 1
        try:
            yield snapshots
        finally:
            with self._lock:
                self._pins[submission_id] -= 1
                if not self._pins[submission_id]:
                    del self._pins[submission_id]
                    self._cleanup_submission(submission_id)

    def acknowledge(self, receipt: AcceptanceReceipt) -> None:
        with self._lock:
            safe_component(receipt.submission_id)
            if receipt.sender_id != self.sender_id:
                raise ValueError("foreign acknowledgment")
            row = self._db.execute(
                "SELECT payload_sha256 FROM identities WHERE submission_id=?",
                (receipt.submission_id,),
            ).fetchone()
            if row is None:
                return
            if receipt.payload_sha256 != row[0]:
                raise ValueError("acknowledgment digest mismatch")
            with self._db:
                self._db.execute(
                    "UPDATE envelopes SET pending=0 WHERE submission_id=?", (receipt.submission_id,)
                )
            self._cleanup_submission(receipt.submission_id)

    def _cleanup_submission(self, submission_id: str) -> None:
        if self._pins.get(submission_id):
            return
        row = self._db.execute(
            "SELECT pending FROM envelopes WHERE submission_id=?", (submission_id,)
        ).fetchone()
        if row is None or row[0]:
            return
        folder = self._entries / submission_id
        if folder.exists():
            shutil.rmtree(folder)
            fsync_directory(self._entries)

    def _cleanup_acknowledged(self) -> None:
        for (submission_id,) in self._db.execute(
            "SELECT submission_id FROM envelopes WHERE pending=0"
        ):
            self._cleanup_submission(submission_id)
