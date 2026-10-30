"""Immutable artifact snapshots and bounded byte transfer."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from planner.executor.configuration import FILE_CHUNK_BYTES
from planner.executor.protocol import FileManifest, FileScope, FileSnapshot


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
        FileManifest(scope, owner_id, relative_path, digest.hexdigest(), count),
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
