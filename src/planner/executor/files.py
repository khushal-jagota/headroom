"""Immutable artifact snapshots and bounded byte transfer without a local database."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

from planner.executor.configuration import FILE_CHUNK_BYTES, ExecutorPaths
from planner.executor.contracts import FileChunk, FileManifest, FileScope, FileSnapshot


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def safe_component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("unsafe executor identifier")
    return value


def contained_path(root: Path, relative_path: str) -> Path:
    if (
        not relative_path
        or "\\" in relative_path
        or re.search(r"%(25|2e|2f|5c)", relative_path, re.I)
        or any(part in ("", ".", "..") for part in relative_path.split("/"))
    ):
        raise ValueError("unsafe relative artifact path")
    current = root
    if root.is_symlink():
        raise ValueError("artifact root is symlink")
    for part in relative_path.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError("artifact path contains symlink")
    current.resolve().relative_to(root.resolve())
    return current


def snapshot_file(
    *,
    root: Path,
    relative_path: str,
    scope: FileScope,
    owner_id: str,
) -> FileSnapshot:
    safe_component(owner_id)
    path = contained_path(root, relative_path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("artifact is not regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            contents = source.read()
    finally:
        os.close(descriptor)
    private = root.parent / ".executor-snapshots"
    private.mkdir(mode=0o700, exist_ok=True)
    temporary_descriptor, temporary_path = tempfile.mkstemp(dir=private)
    with os.fdopen(temporary_descriptor, "wb") as snapshot:
        snapshot.write(contents)
        snapshot.flush()
        os.fsync(snapshot.fileno())
    return FileSnapshot(
        FileManifest(
            publication_id=uuid4().hex,
            scope=scope,
            owner_id=owner_id,
            relative_path=relative_path,
            sha256=hashlib.sha256(contents).hexdigest(),
            byte_count=len(contents),
        ),
        Path(temporary_path),
    )


def snapshot_ticket(paths: ExecutorPaths, ticket_id: str) -> tuple[FileSnapshot, ...]:
    root = paths.artifacts / safe_component(ticket_id)
    if not root.exists():
        return ()
    return tuple(
        snapshot_file(
            root=root,
            relative_path=path.relative_to(root).as_posix(),
            scope=FileScope.ticket,
            owner_id=ticket_id,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() or path.is_symlink()
    )


def file_chunks(snapshot: FileSnapshot) -> tuple[FileChunk, ...]:
    contents = snapshot.local_path.read_bytes()
    if (
        len(contents) != snapshot.manifest.byte_count
        or hashlib.sha256(contents).hexdigest() != snapshot.manifest.sha256
    ):
        raise ValueError("file snapshot changed")
    return tuple(
        FileChunk(
            snapshot.manifest.publication_id,
            offset,
            base64.b64encode(contents[offset : offset + FILE_CHUNK_BYTES]).decode(),
        )
        for offset in range(0, len(contents), FILE_CHUNK_BYTES)
    )


class IncomingFiles:
    """Quarantined VPS inputs become native-readable only after full-byte verification."""

    def __init__(self, root: Path, quarantine: Path) -> None:
        self.root = root
        self.quarantine = quarantine
        self._manifests: dict[str, FileManifest] = {}

    def begin(self, manifest: FileManifest) -> None:
        safe_component(manifest.publication_id)
        safe_component(manifest.owner_id)
        contained_path(self.root, manifest.relative_path)
        previous = self._manifests.get(manifest.publication_id)
        if previous is not None and previous != manifest:
            raise ValueError("conflicting input manifest")
        self.quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.quarantine / manifest.publication_id
        # A repeated manifest begins a full retry from offset zero. The sender
        # serializes transfers; partial bytes from an earlier link are not resumable.
        target.write_bytes(b"")
        self._manifests[manifest.publication_id] = manifest

    def chunk(self, chunk: FileChunk) -> None:
        manifest = self._manifests[chunk.publication_id]
        contents = base64.b64decode(chunk.content_base64, validate=True)
        if len(contents) > FILE_CHUNK_BYTES:
            raise ValueError("input chunk too large")
        target = self.quarantine / chunk.publication_id
        if (
            target.stat().st_size != chunk.offset
            or chunk.offset + len(contents) > manifest.byte_count
        ):
            raise ValueError("input chunk offset or size invalid")
        with target.open("ab") as stream:
            stream.write(contents)

    def complete(self, manifest: FileManifest) -> Path:
        if self._manifests.get(manifest.publication_id) != manifest:
            raise ValueError("unknown input publication")
        temporary = self.quarantine / manifest.publication_id
        contents = temporary.read_bytes()
        if (
            len(contents) != manifest.byte_count
            or hashlib.sha256(contents).hexdigest() != manifest.sha256
        ):
            raise ValueError("incomplete or corrupt input")
        family = "conversations" if manifest.scope == FileScope.conversation else "tickets"
        target = contained_path(
            self.root, f"{family}/{safe_component(manifest.owner_id)}/{manifest.relative_path}"
        )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        fsync_directory(target.parent)
        self._manifests.pop(manifest.publication_id)
        return target
