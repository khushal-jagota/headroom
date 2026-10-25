"""Quarantined VPS file reception with recoverable, ordered publication."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from planner.executor.configuration import FILE_CHUNK_BYTES
from planner.executor.contracts import ExecutorIdentity, FileManifest
from planner.executor.files import (
    contained_path,
    copy_verified,
    directory_descriptor,
    fsync_directory,
    regular_descriptor,
    safe_component,
)
from planner.executor.store import ExecutorStore


def _matches(path: Path, manifest: FileManifest) -> bool:
    try:
        with regular_descriptor(path) as descriptor:
            if os.fstat(descriptor).st_size != manifest.byte_count:
                return False
            digest = hashlib.sha256()
            while block := os.read(descriptor, FILE_CHUNK_BYTES):
                digest.update(block)
            return digest.hexdigest() == manifest.sha256
    except FileNotFoundError:
        return False


class PublicationReceiver:
    """files_root holds canonical tickets/ and conversations/; quarantine is unserved."""

    def __init__(self, store: ExecutorStore, files_root: Path, quarantine_root: Path) -> None:
        self.store = store
        self.files_root = files_root.absolute()
        self.quarantine_root = quarantine_root.absolute()
        if self.quarantine_root.is_relative_to(self.files_root):
            raise ValueError("Quarantine must be outside the served file root")
        with directory_descriptor(self.quarantine_root, create=True):
            pass

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

    def ingest_staged(
        self, manifest: FileManifest, staged_path: Path, *, identity: ExecutorIdentity
    ) -> None:
        self.store.verify_connection(identity)
        self.begin(manifest, identity=identity)
        if self.store.publication_accepted(manifest):
            return
        temporary = self.quarantine_root / (uuid4().hex + ".receiver-copy")
        copy_verified(staged_path, temporary, manifest)
        try:
            self.store.verify_connection(identity)
            with directory_descriptor(self.quarantine_root) as parent:
                os.replace(
                    temporary.name,
                    self._partial(manifest.publication_id).name,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                )
                os.fsync(parent)
        finally:
            temporary.unlink(missing_ok=True)

    def complete(self, manifest: FileManifest, now_ms: int, *, identity: ExecutorIdentity) -> None:
        target = self._target(manifest)
        partial = self._partial(manifest.publication_id)

        def publish() -> None:
            with directory_descriptor(target.parent, create=True) as parent:
                self._target(manifest)
                if _matches(partial, manifest):
                    with directory_descriptor(self.quarantine_root) as quarantine:
                        os.replace(
                            partial.name, target.name, src_dir_fd=quarantine, dst_dir_fd=parent
                        )
                        os.fsync(quarantine)
                    os.fsync(parent)
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
