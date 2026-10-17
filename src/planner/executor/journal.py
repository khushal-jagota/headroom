"""One crash-recoverable filesystem journal for Mac-authored unaccepted work."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from planner.executor.configuration import SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileManifest,
    FileSnapshot,
    JsonObject,
    QueuedEnvelope,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import fsync_directory, safe_component
from planner.executor.preflight import _check_spool_directory
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


def _write(path: Path, contents: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


class FilesystemSpool:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        _check_spool_directory(root, SPOOL_FORMAT_VERSION)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._process_lock = (root / "lock").open("a+b")
        fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck existing versioned state under the lifetime lock. A fresh lock-only
        # directory is our bootstrap and has no authored work to inspect yet.
        if (root / "metadata.json").exists():
            try:
                _check_spool_directory(root, SPOOL_FORMAT_VERSION)
            except Exception:
                self.close()
                raise
        self._entries = root / "entries"
        self._entries.mkdir(exist_ok=True, mode=0o700)
        metadata = root / "metadata.json"
        if metadata.exists():
            data = json.loads(metadata.read_bytes())
            if data["format"] != SPOOL_FORMAT_VERSION:
                self.close()
                raise ValueError("incompatible spool; preserved without mutation")
            self._sender_id = str(data["sender_id"])
            self._identities: dict[str, list[str | int]] = data["identities"]
            self._sequences: dict[str, int] = data["sequences"]
        else:
            self._sender_id = uuid4().hex
            self._identities = {}
            self._sequences = {}
        for envelope in self.pending():
            self._remember(envelope)
        self._save_metadata()
        # Unpublished writes are never replayed. Preserve them for diagnosis.

    @property
    def sender_id(self) -> str:
        return self._sender_id

    def close(self) -> None:
        self._process_lock.close()

    def _save_metadata(self) -> None:
        temporary = self.root / f".metadata-{uuid4().hex}"
        _write(
            temporary,
            json.dumps(
                {
                    "format": SPOOL_FORMAT_VERSION,
                    "sender_id": self.sender_id,
                    "identities": self._identities,
                    "sequences": self._sequences,
                },
                sort_keys=True,
            ).encode(),
        )
        os.replace(temporary, self.root / "metadata.json")
        fsync_directory(self.root)

    def _remember(self, envelope: QueuedEnvelope) -> None:
        identity: list[str | int] = [
            envelope.conversation_id,
            envelope.sequence,
            envelope.payload_sha256,
        ]
        existing = self._identities.get(envelope.submission_id)
        if existing is not None and existing != identity:
            raise ValueError("submission identity conflicts with journal")
        self._identities[envelope.submission_id] = identity
        self._sequences[envelope.conversation_id] = max(
            envelope.sequence, self._sequences.get(envelope.conversation_id, 0)
        )

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
            identity = submission_id or uuid4().hex
            safe_component(identity)
            manifests = tuple(snapshot.manifest for snapshot in snapshots)
            previous = self._identities.get(identity)
            if previous is not None:
                sequence = int(previous[1])
            else:
                sequence = self._sequences.get(conversation_id, 0) + 1
            digest = envelope_payload_digest(
                payload,
                manifests,
                sender_id=self.sender_id,
                conversation_id=conversation_id,
                sequence=sequence,
                submission_id=identity,
                kind=kind,
            )
            if previous is not None and (previous[0] != conversation_id or previous[2] != digest):
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
            # No local queued promise for a row that the agreed wire cannot carry.
            encode_frame(
                WireMessage(
                    WireMessageKind.envelope,
                    "0" * 32,
                    TypeAdapter(QueuedEnvelope).dump_python(envelope, mode="json"),
                )
            )
            safe_component(envelope.submission_id)
            if envelope.sender_id != self.sender_id:
                raise ValueError("foreign sender")
            if envelope_payload_digest(
                envelope.payload,
                envelope.file_dependencies,
                sender_id=envelope.sender_id,
                conversation_id=envelope.conversation_id,
                sequence=envelope.sequence,
                submission_id=envelope.submission_id,
                kind=envelope.kind,
            ) != (envelope.payload_sha256):
                raise ValueError("invalid envelope digest")
            target = self._entries / envelope.submission_id
            if target.exists():
                if (
                    TypeAdapter(QueuedEnvelope).validate_json(
                        (target / "envelope.json").read_bytes()
                    )
                    != envelope
                ):
                    raise ValueError("conflicting immutable entry")
                return
            if tuple(snapshot.manifest for snapshot in snapshots) != envelope.file_dependencies:
                raise ValueError("file dependencies lack snapshots")
            temporary = self.root / f".incomplete-{uuid4().hex}"
            temporary.mkdir(mode=0o700)
            for index, snapshot in enumerate(snapshots):
                contents = snapshot.local_path.read_bytes()
                if (
                    len(contents) != snapshot.manifest.byte_count
                    or hashlib.sha256(contents).hexdigest() != snapshot.manifest.sha256
                ):
                    raise ValueError("snapshot changed before durable publication")
                _write(temporary / f"file-{index}", contents)
            _write(temporary / "envelope.json", TypeAdapter(QueuedEnvelope).dump_json(envelope))
            fsync_directory(temporary)
            os.rename(temporary, target)
            fsync_directory(self._entries)
            self._remember(envelope)
            self._save_metadata()
            for snapshot in snapshots:
                if snapshot.local_path.parent.name == ".executor-snapshots":
                    snapshot.local_path.unlink(missing_ok=True)

    def pending(self) -> tuple[QueuedEnvelope, ...]:
        with self._lock:
            rows = [
                TypeAdapter(QueuedEnvelope).validate_json((path / "envelope.json").read_bytes())
                for path in self._entries.iterdir()
                if path.is_dir()
            ]
            return tuple(sorted(rows, key=lambda value: (value.conversation_id, value.sequence)))

    def snapshots(self, submission_id: str) -> tuple[FileSnapshot, ...]:
        with self._lock:
            folder = self._entries / safe_component(submission_id)
            envelope = TypeAdapter(QueuedEnvelope).validate_json(
                (folder / "envelope.json").read_bytes()
            )
            return tuple(
                FileSnapshot(manifest, folder / f"file-{index}")
                for index, manifest in enumerate(envelope.file_dependencies)
            )

    def acknowledge(self, receipt: AcceptanceReceipt) -> None:
        with self._lock:
            folder = self._entries / safe_component(receipt.submission_id)
            if receipt.sender_id != self.sender_id:
                raise ValueError("foreign acknowledgment")
            if not folder.exists():
                return
            envelope = TypeAdapter(QueuedEnvelope).validate_json(
                (folder / "envelope.json").read_bytes()
            )
            if receipt.payload_sha256 != envelope.payload_sha256:
                raise ValueError("acknowledgment digest mismatch")
            self._remember(envelope)
            self._save_metadata()
            # First move outside replay, then fsync; interrupted cleanup cannot replay it.
            trash = self.root / f".acknowledged-{uuid4().hex}"
            os.rename(folder, trash)
            fsync_directory(self._entries)
            fsync_directory(self.root)
            shutil.rmtree(trash)
            fsync_directory(self.root)
