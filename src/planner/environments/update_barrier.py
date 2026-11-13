"""A rollback-safe cut for output receipts, outside the database snapshot."""

from __future__ import annotations

import asyncio
import fcntl
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class UpdateBarrier:
    """Cache the durable gate and count the actual saves, including detached saves."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.frozen = path.exists()
        self._lock = threading.RLock()
        self._generation = self._durable_generation = 0
        self._active = 0
        self._drained = asyncio.Event()
        self._drained.set()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def reopen_if_current(self, generation: int) -> int | None:
        with self._lock:
            if generation != self._generation:
                return None
            self._generation += 1
            self.frozen = False
            return self._generation

    def close(self) -> int:
        with self._lock:
            self._generation += 1
            self.frozen = True
            return self._generation

    def reopen(self) -> int:
        with self._lock:
            self._generation += 1
            self.frozen = False
            return self._generation

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        with self.path.with_suffix(".json.lock").open("a+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            yield

    def persist(self, attempt_id: str, generation: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=".update-barrier-")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(attempt_id + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with self._lock, self._file_lock():
                if generation != self._generation:
                    return
                temporary.replace(self.path)
                self._durable_generation = generation
            self._sync_directory()
        finally:
            temporary.unlink(missing_ok=True)

    def freeze(self, attempt_id: str) -> None:
        self.persist(attempt_id, self.close())

    def _sync_directory(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @property
    def drained(self) -> bool:
        return self._active == 0

    def remove(self, generation: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._file_lock():
            if generation != self._generation:
                return
            self.path.unlink(missing_ok=True)
            self._durable_generation = generation
        self._sync_directory()

    def release(self) -> None:
        self.remove(self.reopen())

    def refresh(self) -> None:
        """Do not let an old disk marker override a pending local cancellation."""
        with self._lock:
            if self._durable_generation == self._generation:
                self.frozen = self.path.exists()

    @contextmanager
    def installer_guard(self, _attempt_id: str) -> Iterator[bool]:
        """Keep an absent installer from a late claim until recovery marks it failed."""
        with (self.path.parent / "update-installer.lock").open("a+") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # The owner can hold the lock before it publishes its attempt ID.
                # Stale or missing text cannot prove that the owner is gone.
                yield True
            else:
                try:
                    yield False
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def installer_active(self, attempt_id: str) -> bool:
        """Any held lifetime lock proves an installer or restore can still write."""
        with self.installer_guard(attempt_id) as active:
            return active

    @contextmanager
    def operation(self) -> Iterator[bool]:
        admitted = not self.frozen
        if admitted:
            self._active += 1
            self._drained.clear()
        try:
            yield admitted
        finally:
            if admitted:
                self._active -= 1
                if self._active == 0:
                    self._drained.set()

    async def drain(self) -> None:
        await self._drained.wait()
