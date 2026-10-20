"""Keep kernel signal provenance until the supervisor dispatches it."""

from __future__ import annotations

import os
import queue
import signal
import sys
import threading
from pathlib import Path
from types import FrameType

from planner.server_lifecycle.contracts import ServerLifecycleError

WATCHED_SIGNALS: set[int] = {signal.SIGINT, signal.SIGTERM, signal.SIGCHLD}


class SupervisorSignals:
    """One bounded waiter and a nonblocking selector notification pipe."""

    def __init__(self) -> None:
        self.read_fd = -1
        self._write_fd = -1
        self._events: queue.SimpleQueue[tuple[int, int | None]] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._capture_lock = threading.Condition()
        self._drain_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous_mask: set[int] = set()
        self._previous_handlers: dict[int, signal._HANDLER] = {}
        self._linux = sys.platform == "linux"

    @property
    def child_signal_mask(self) -> set[int]:
        return self._previous_mask

    def start(self) -> None:
        if (
            threading.current_thread() is not threading.main_thread()
            or threading.active_count() != 1
        ):
            raise ServerLifecycleError("Supervisor signal setup requires a single main thread.")
        if self._linux and len(list(Path("/proc/self/task").iterdir())) != 1:
            raise ServerLifecycleError("Supervisor signal setup requires one native thread.")
        self._previous_handlers = {item: signal.getsignal(item) for item in WATCHED_SIGNALS}
        self._previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, WATCHED_SIGNALS if self._linux else set()
        )
        try:
            self.read_fd, self._write_fd = (
                os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC) if self._linux else os.pipe()
            )
            os.set_blocking(self.read_fd, False)
            os.set_blocking(self._write_fd, False)
            if self._linux:
                self._thread = threading.Thread(
                    target=self._wait, name="supervisor-signals", daemon=True
                )
                self._thread.start()
            else:
                # Non-Linux development keeps ordinary sender-blind shutdown behavior.
                for item in WATCHED_SIGNALS:
                    signal.signal(item, self._receive_ordinary_signal)
        except BaseException:
            for descriptor in (self.read_fd, self._write_fd):
                if descriptor >= 0:
                    os.close(descriptor)
            if self._linux:
                for item in WATCHED_SIGNALS:
                    signal.signal(item, signal.SIG_IGN)
                signal.pthread_sigmask(signal.SIG_SETMASK, self._previous_mask)
            for item, handler in self._previous_handlers.items():
                signal.signal(item, handler)
            raise

    def _receive_ordinary_signal(self, signum: int, _frame: FrameType | None) -> None:
        self._notify(signum, None)

    def _notify(self, signum: int, sender_pid: int | None) -> None:
        self._events.put((signum, sender_pid))
        try:
            os.write(self._write_fd, b"s")
        except BlockingIOError:
            pass  # The readable pipe already guarantees a selector wakeup.

    def _wait(self) -> None:
        while not self._stop.is_set():
            with self._capture_lock:
                while self._drain_requested.is_set() and not self._stop.is_set():
                    self._capture_lock.wait()
                if self._stop.is_set():
                    return
                info = signal.sigtimedwait(WATCHED_SIGNALS, 0.1)
                if info is not None:
                    self._notify(info.si_signo, info.si_pid)

    def drain(self) -> list[tuple[int, int | None]]:
        # Capture and publication share one lock. Main-thread drains therefore see
        # both kernel-pending signals and signals already consumed by the waiter.
        # Set priority before acquiring the lock. The waiter must yield after its
        # current bounded wait, rather than repeatedly reacquire ahead of the main thread.
        self._drain_requested.set()
        with self._capture_lock:
            try:
                if self._linux:
                    while (info := signal.sigtimedwait(WATCHED_SIGNALS, 0)) is not None:
                        self._events.put((info.si_signo, info.si_pid))
                return self._drain_notifications()
            finally:
                self._drain_requested.clear()
                self._capture_lock.notify_all()

    def _drain_notifications(self) -> list[tuple[int, int | None]]:
        try:
            while os.read(self.read_fd, 4096):
                pass
        except BlockingIOError:
            pass
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def stop(self) -> list[tuple[int, int | None]]:
        self._stop.set()
        with self._capture_lock:
            self._capture_lock.notify_all()
        if self._thread is not None:
            self._thread.join()
        if self._linux:
            # Keep signals blocked while collecting the last kernel-pending events.
            while (info := signal.sigtimedwait(WATCHED_SIGNALS, 0)) is not None:
                self._notify(info.si_signo, info.si_pid)
            # Ignore before unblocking: a refused signal must never become default
            # delivery in the teardown gap, including signals arriving after the drain.
            for item in WATCHED_SIGNALS:
                signal.signal(item, signal.SIG_IGN)
        events = self.drain()
        if self._linux:
            signal.pthread_sigmask(signal.SIG_SETMASK, self._previous_mask)
        for item, handler in self._previous_handlers.items():
            signal.signal(item, handler)
        os.close(self.read_fd)
        os.close(self._write_fd)
        return events
