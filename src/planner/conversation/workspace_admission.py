"""One small admission barrier shared by conversations and background starts."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar


class WorkspaceAdmission:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._starts_closed = False
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

    @property
    def starts_closed(self) -> bool:
        with self._lock:
            return self._starts_closed

    def close_starts(self) -> bool:
        """Pause new turns while admitted work can still save its results."""
        with self._lock:
            if self._closed or self._starts_closed:
                return False
            self._starts_closed = True
            return True

    @contextmanager
    def operation(self, *, starts: bool = False) -> Iterator[bool]:
        with self._lock:
            admitted = not self._closed and not (starts and self._starts_closed)
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
            self._starts_closed = True
            return True

    def reopen(self) -> None:
        """Open work again after an update that paused it could not start its installer."""
        with self._lock:
            self._closed = False
            self._starts_closed = False


class RequestAdmissionLease:
    """Let a request release admission before a writer sleeps without any effects."""

    def __init__(self, admission: WorkspaceAdmission) -> None:
        self.admission = admission
        self._lock = threading.RLock()
        self._context: AbstractContextManager[bool] | None = None
        self._finished = False

    def resume(self) -> bool:
        with self._lock:
            if self._finished:
                return False
            if self._context is not None:
                return True
            context = self.admission.operation()
            if not context.__enter__():
                context.__exit__(None, None, None)
                return False
            self._context = context
            return True

    def suspend(self) -> None:
        with self._lock:
            if self._context is not None:
                context, self._context = self._context, None
                context.__exit__(None, None, None)

    def close(self) -> None:
        with self._lock:
            self._finished = True
            self.suspend()


# AnyIO copies this request context into the synchronous proposal writer's thread.
current_write_admission: ContextVar[RequestAdmissionLease | None] = ContextVar(
    "current_write_admission", default=None
)
