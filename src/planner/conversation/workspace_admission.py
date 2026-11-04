"""One small admission barrier shared by conversations and background starts."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class WorkspaceAdmission:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._active = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._active)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @contextmanager
    def operation(self) -> Iterator[bool]:
        with self._lock:
            admitted = not self._closed
            if admitted:
                self._active += 1
        try:
            yield admitted
        finally:
            if admitted:
                with self._lock:
                    self._active -= 1

    def claim(
        self, operation: Callable[[], None], *, idle: Callable[[], bool], require_idle: bool
    ) -> bool:
        with self._lock:
            if self._closed or (require_idle and (self._active or not idle())):
                return False
            operation()
            self._closed = True
            return True
