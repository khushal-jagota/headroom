"""One paired process inventory and indexed discovery for all local Workers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from planner.worker_supervision import config
from planner.worker_supervision.contracts import (
    InspectionContext,
    ProcessObservation,
    WorkerProcess,
)


def index_processes(
    table: dict[int, WorkerProcess],
) -> tuple[dict[int, list[WorkerProcess]], dict[int, list[WorkerProcess]]]:
    children: dict[int, list[WorkerProcess]] = {}
    sessions: dict[int, list[WorkerProcess]] = {}
    for process in table.values():
        if not process.zombie:
            children.setdefault(process.parent, []).append(process)
            sessions.setdefault(process.session, []).append(process)
    return children, sessions


def capture_observation(context: InspectionContext, deadline: float) -> ProcessObservation:
    first = context.primitives.snapshot(deadline)
    second = context.primitives.snapshot(deadline)
    # Neither sequential inventory is atomic. Both ends of a relationship must
    # retain exactly the same incarnation and relationship across the pair.
    stable = {pid: process for pid, process in second.items() if first.get(pid) == process}
    children, sessions = index_processes(stable)
    _, latest_sessions = index_processes(second)
    return ProcessObservation(stable, children, sessions, latest_sessions)


ObservationCallback = Callable[[InspectionContext | None, ProcessObservation | None], None]


class SharedProcessObserver:
    def __init__(self, context_factory: Callable[[float], InspectionContext]) -> None:
        self._context_factory = context_factory
        self._condition = threading.Condition()
        self._callbacks: dict[Path, ObservationCallback] = {}
        self._thread: threading.Thread | None = None

    def register(self, path: Path, callback: ObservationCallback) -> None:
        with self._condition:
            self._callbacks[path] = callback
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="worker-process-custody", daemon=True
                )
                self._thread.start()

    def unregister(self, path: Path) -> None:
        with self._condition:
            self._callbacks.pop(path, None)
            self._condition.notify_all()

    def _run(self) -> None:
        context: InspectionContext | None = None
        due = time.monotonic() + config.OBSERVATION_SECONDS
        while True:
            with self._condition:
                if not self._callbacks:
                    # Registration and thread retirement share this lock, so a
                    # concurrent registration either keeps this thread or starts one.
                    self._thread = None
                    return
                remaining = due - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                callbacks = tuple(self._callbacks.values())
            observation = None
            deadline = time.monotonic() + config.INSPECTION_SECONDS
            try:
                if context is None:
                    context = self._context_factory(deadline)
                observation = capture_observation(context, deadline)
            except (OSError, ValueError, TypeError, KeyError, TimeoutError):
                pass
            for callback in callbacks:
                callback(context, observation)
            due = time.monotonic() + config.OBSERVATION_SECONDS
