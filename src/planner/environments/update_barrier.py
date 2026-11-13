"""A rollback-safe cut for output receipts, outside the database snapshot."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class UpdateBarrier:
    """Cache the durable gate and count the actual saves, including detached saves."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.frozen = path.exists()
        self._active = 0
        self._drained = asyncio.Event()
        self._drained.set()

    def freeze(self, attempt_id: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            stream.write(attempt_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        self._sync_directory()
        self.frozen = True

    def _sync_directory(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @property
    def drained(self) -> bool:
        return self._active == 0

    def release(self) -> None:
        if not self.drained:
            raise RuntimeError("Output saves still hold the update barrier.")
        self.path.unlink(missing_ok=True)
        self._sync_directory()
        self.frozen = False

    def refresh(self) -> None:
        """The shared observer calls this after an attempt or marker change."""
        self.frozen = self.path.exists()

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
