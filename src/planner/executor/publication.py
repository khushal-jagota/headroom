"""Quarantined VPS file reception with recoverable, ordered publication."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from pathlib import Path

from planner.executor.configuration import FILE_CHUNK_BYTES
from planner.executor.contracts import ExecutorIdentity, FileChunk, FileManifest
from planner.executor.files import contained_path, fsync_directory, safe_component
from planner.executor.store import ExecutorStore


def _mkdir_durable(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        fsync_directory(directory)
        fsync_directory(directory.parent)


def _matches(path: Path, manifest: FileManifest) -> bool:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != manifest.byte_count:
            return False
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while block := stream.read(FILE_CHUNK_BYTES):
                digest.update(block)
        return digest.hexdigest() == manifest.sha256
    finally:
        os.close(descriptor)


class PublicationReceiver:
    """files_root holds canonical tickets/ and conversations/; quarantine is unserved."""

    def __init__(self, store: ExecutorStore, files_root: Path, quarantine_root: Path) -> None:
        self.store = store
        self.files_root = files_root.resolve()
        self.quarantine_root = quarantine_root.resolve()
        if self.quarantine_root.is_relative_to(self.files_root):
            raise ValueError("Quarantine must be outside the served file root")
        self.quarantine_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _target(self, manifest: FileManifest) -> Path:
        safe_component(manifest.owner_id)
        if manifest.byte_count < 0 or not re.fullmatch(r"[0-9a-f]{64}", manifest.sha256):
            raise ValueError("Invalid file length or digest")
        scope = "tickets" if manifest.scope.value == "ticket" else "conversations"
        scope_root = contained_path(self.files_root, scope + "/" + manifest.owner_id)
        return contained_path(scope_root, manifest.relative_path)

    def _partial(self, publication_id: str) -> Path:
        return self.quarantine_root / (safe_component(publication_id) + ".partial")

    def begin(self, manifest: FileManifest, *, identity: ExecutorIdentity) -> None:
        self._target(manifest)
        self.store.begin_publication(manifest, identity=identity)
        if self.store.publication_accepted(manifest):
            return
        path = self._partial(manifest.publication_id)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Invalid quarantine file") from None
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(self.quarantine_root)

    def write_chunk(self, chunk: FileChunk, *, identity: ExecutorIdentity) -> None:
        self.store.verify_connection(identity)
        manifest = self.store.publication_manifest(chunk.publication_id)
        if manifest is None:
            raise ValueError("File chunk has no manifest")
        if self.store.publication_accepted(manifest):
            return
        contents = base64.b64decode(chunk.content_base64, validate=True)
        if (
            len(contents) > FILE_CHUNK_BYTES
            or chunk.offset < 0
            or chunk.offset + len(contents) > manifest.byte_count
        ):
            raise ValueError("File chunk exceeds manifest bounds")
        path = self._partial(chunk.publication_id)
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            current_size = os.fstat(descriptor).st_size
            if chunk.offset < current_size:
                if os.pread(descriptor, len(contents), chunk.offset) != contents:
                    raise ValueError("Repeated file chunk conflicts")
                return
            if chunk.offset != current_size:
                raise ValueError("File chunk is out of order")
            written = 0
            while written < len(contents):
                count = os.pwrite(descriptor, contents[written:], chunk.offset + written)
                if count == 0:
                    raise OSError("File chunk write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def complete(self, manifest: FileManifest, now_ms: int, *, identity: ExecutorIdentity) -> None:
        target = self._target(manifest)
        partial = self._partial(manifest.publication_id)

        def publish() -> None:
            _mkdir_durable(target.parent)
            # Recheck after mkdir and inside the path-serialization transaction.
            self._target(manifest)
            if _matches(partial, manifest):
                os.replace(partial, target)
                fsync_directory(target.parent)
                fsync_directory(self.quarantine_root)
                return
            # Crash after rename, before DB commit: complete bytes are an orphan,
            # and the staged reservation prevents a later version from passing it.
            if _matches(target, manifest):
                descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                fsync_directory(target.parent)
                return
            raise ValueError("File bytes are incomplete or corrupt")

        self.store.accept_publication(manifest, publish, now_ms, identity=identity)
