"""Immutable artifact snapshots and bounded byte transfer."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from planner.executor.configuration import FILE_CHUNK_BYTES, ExecutorPaths
from planner.executor.contracts import FileManifest, FileScope, FileSnapshot


def fsync_file(descriptor: int) -> None:
    """Persist regular-file bytes, including the drive cache on macOS."""
    os.fsync(descriptor)
    if sys.platform == "darwin":
        fcntl.fcntl(descriptor, fcntl.F_FULLFSYNC)


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


@contextmanager
def directory_descriptor(path: Path, *, create: bool = False) -> Iterator[int]:
    """Walk every directory with O_NOFOLLOW, then retain its inode through I/O."""
    absolute = path.absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if component in (".", ".."):
                raise ValueError("unsafe directory component")
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    fsync_file(descriptor)
                except FileExistsError:
                    pass
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def regular_descriptor(path: Path) -> Iterator[int]:
    with directory_descriptor(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("artifact is not regular file")
        yield descriptor
    finally:
        os.close(descriptor)


def copy_verified(source: Path, destination: Path, manifest: FileManifest) -> None:
    """Copy bounded blocks into a new inode and verify complete bytes."""
    with regular_descriptor(source) as incoming, directory_descriptor(destination.parent) as parent:
        outgoing = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            digest = hashlib.sha256()
            count = 0
            while block := os.read(incoming, FILE_CHUNK_BYTES):
                count += len(block)
                if count > manifest.byte_count:
                    raise ValueError("file exceeds manifest length")
                digest.update(block)
                view = memoryview(block)
                while view:
                    written = os.write(outgoing, view)
                    if not written:
                        raise OSError("file write made no progress")
                    view = view[written:]
            if count != manifest.byte_count or digest.hexdigest() != manifest.sha256:
                raise ValueError("incomplete or corrupt file")
            fsync_file(outgoing)
        except BaseException:
            os.unlink(destination.name, dir_fd=parent)
            raise
        finally:
            os.close(outgoing)


def snapshot_file(
    *,
    root: Path,
    relative_path: str,
    scope: FileScope,
    owner_id: str,
) -> FileSnapshot:
    safe_component(owner_id)
    path = contained_path(root, relative_path)
    private = root.parent / ".executor-snapshots"
    with directory_descriptor(private, create=True):
        pass
    temporary_path = private / uuid4().hex
    digest = hashlib.sha256()
    count = 0
    with regular_descriptor(path) as incoming, directory_descriptor(private) as parent:
        outgoing = os.open(
            temporary_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(outgoing, "wb", closefd=False) as snapshot:
                while block := os.read(incoming, FILE_CHUNK_BYTES):
                    snapshot.write(block)
                    digest.update(block)
                    count += len(block)
                snapshot.flush()
                fsync_file(outgoing)
        except BaseException:
            os.unlink(temporary_path.name, dir_fd=parent)
            raise
        finally:
            os.close(outgoing)
    return FileSnapshot(
        FileManifest(uuid4().hex, scope, owner_id, relative_path, digest.hexdigest(), count),
        temporary_path,
    )


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    with regular_descriptor(path) as incoming:
        while block := os.read(incoming, FILE_CHUNK_BYTES):
            digest.update(block)
            count += len(block)
    return digest.hexdigest(), count


def snapshot_ticket(
    paths: ExecutorPaths,
    ticket_id: str,
    *,
    previous: Mapping[str, FileManifest] | None = None,
    reuse: Callable[[FileManifest], FileSnapshot | None] | None = None,
) -> tuple[FileSnapshot, ...]:
    root = paths.artifacts / safe_component(ticket_id)
    if not root.exists():
        return ()
    snapshots: list[FileSnapshot] = []
    try:
        for path in sorted(root.rglob("*")):
            if not (path.is_file() or path.is_symlink()):
                continue
            relative = path.relative_to(root).as_posix()
            digest = file_digest(path)
            manifest = previous.get(relative) if previous is not None else None
            if manifest is not None and (manifest.sha256, manifest.byte_count) == digest:
                assert reuse is not None
                retained = reuse(manifest)
                if retained is not None:
                    snapshots.append(retained)
                continue
            snapshot = snapshot_file(
                root=root, relative_path=relative, scope=FileScope.ticket, owner_id=ticket_id
            )
            snapshots.append(snapshot)
            if (snapshot.manifest.sha256, snapshot.manifest.byte_count) != digest:
                raise ValueError("artifact changed during snapshot capture")
        return tuple(snapshots)
    except BaseException:
        for snapshot in snapshots:
            if snapshot.local_path.parent.name == ".executor-snapshots":
                snapshot.local_path.unlink(missing_ok=True)
        raise


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

    def receive_path(self, manifest: FileManifest, path: Path) -> Path:
        safe_component(manifest.publication_id)
        safe_component(manifest.owner_id)
        contained_path(self.root, manifest.relative_path)
        with directory_descriptor(self.quarantine, create=True) as parent:
            temporary = self.quarantine / uuid4().hex
            copy_verified(path, temporary, manifest)
            os.replace(
                temporary.name, manifest.publication_id, src_dir_fd=parent, dst_dir_fd=parent
            )
            os.fsync(parent)
        self._manifests[manifest.publication_id] = manifest
        return self.complete(manifest)

    def complete(self, manifest: FileManifest) -> Path:
        if self._manifests.get(manifest.publication_id) != manifest:
            raise ValueError("unknown input publication")
        temporary = self.quarantine / manifest.publication_id
        with regular_descriptor(temporary) as descriptor:
            digest = hashlib.sha256()
            count = 0
            while block := os.read(descriptor, FILE_CHUNK_BYTES):
                count += len(block)
                digest.update(block)
            if count != manifest.byte_count or digest.hexdigest() != manifest.sha256:
                raise ValueError("incomplete or corrupt input")
        family = "conversations" if manifest.scope == FileScope.conversation else "tickets"
        target = contained_path(
            self.root, f"{family}/{safe_component(manifest.owner_id)}/{manifest.relative_path}"
        )
        with directory_descriptor(target.parent, create=True) as parent:
            with regular_descriptor(temporary) as descriptor:
                fsync_file(descriptor)
            with directory_descriptor(self.quarantine) as quarantine:
                os.replace(temporary.name, target.name, src_dir_fd=quarantine, dst_dir_fd=parent)
            os.fsync(parent)
        self._manifests.pop(manifest.publication_id)
        return target
