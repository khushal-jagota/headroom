"""One small admission barrier shared by conversations and background starts."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class WorkspaceAdmission:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._active = 0
        self._operations_finished: set[Callable[[], None]] = set()

    def subscribe_operations_finished(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Wake an interested owner after admitted operations drain, without waking writers."""
        with self._lock:
            self._operations_finished.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._operations_finished.discard(callback)

        return unsubscribe

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
                    callbacks = tuple(self._operations_finished) if self._active == 0 else ()
                for callback in callbacks:
                    try:
                        callback()
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Workspace completion listener failed"
                        )

    def claim(
        self, operation: Callable[[], None], *, idle: Callable[[], bool], require_idle: bool
    ) -> bool:
        with self._lock:
            if self._closed or (require_idle and (self._active or not idle())):
                return False
            operation()
            self._closed = True
            return True

    def reopen(self) -> None:
        """Open work again after an update that paused it could not start its installer."""
        with self._lock:
            self._closed = False
