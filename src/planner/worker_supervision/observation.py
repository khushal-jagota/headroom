"""One paired process inventory and indexed discovery for all local Workers."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

from planner.conversation.local_child_contracts import LocalHostBootIdentity
from planner.worker_supervision import config
from planner.worker_supervision.contracts import (
    InspectionContext,
    ProcessObservation,
    WorkerProcess,
    WorkerProcessPrimitives,
)

logger = logging.getLogger(__name__)


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
            if self._thread is None or not self._thread.is_alive():
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
        next_error_log: dict[Path, float] = {}
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
                callbacks = tuple(self._callbacks.items())
                next_error_log = {
                    path: due for path, due in next_error_log.items() if path in self._callbacks
                }
            observation = None
            deadline = time.monotonic() + config.INSPECTION_SECONDS
            try:
                if context is None:
                    context = self._context_factory(deadline)
                observation = capture_observation(context, deadline)
            except (OSError, ValueError, TypeError, KeyError, TimeoutError):
                pass
            for path, callback in callbacks:
                try:
                    callback(context, observation)
                except Exception:
                    # One malformed record cannot end observation of every Worker.
                    now = time.monotonic()
                    if now >= next_error_log.get(path, now):
                        logger.exception("Worker process observation failed for %s", path)
                        next_error_log[path] = now + config.OBSERVATION_ERROR_LOG_SECONDS
            due = time.monotonic() + config.OBSERVATION_SECONDS


FreshObservationKey = tuple[type[WorkerProcessPrimitives], LocalHostBootIdentity]
FreshObservationRequest = tuple[Future[ProcessObservation], float]


class FreshProcessObservations:
    """Coalesce only requests that precede the start of a fresh inventory pair."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: dict[FreshObservationKey, list[FreshObservationRequest]] = {}
        self._capturing: set[FreshObservationKey] = set()

    def capture(self, context: InspectionContext, deadline: float) -> ProcessObservation:
        key = (type(context.primitives), context.host)
        future: Future[ProcessObservation] = Future()
        request = (future, deadline)
        batch: list[FreshObservationRequest] = []
        with self._condition:
            self._pending.setdefault(key, []).append(request)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._remove_pending(key, request)
                    raise TimeoutError("Worker inspection deadline expired")
                if future.done():
                    return future.result()
                if key not in self._capturing:
                    self._capturing.add(key)
                    break
                self._condition.wait(remaining)
        try:
            with self._condition:
                # One bounded wait collects contemporaneous callers. Requests that
                # arrive after this cohort leaves the queue must get the next pair.
                self._condition.wait(
                    min(
                        config.STOP_OBSERVATION_BATCH_SECONDS,
                        max(0, deadline - time.monotonic()) / 4,
                    )
                )
                if time.monotonic() >= deadline:
                    raise TimeoutError("Worker inspection deadline expired")
                batch = self._pending.pop(key)
            pair = capture_observation(context, deadline)
            with self._condition:
                for member, _ in batch:
                    member.set_result(pair)
        except BaseException as error:
            with self._condition:
                for member, member_deadline in batch:
                    if (
                        isinstance(error, TimeoutError)
                        and member is not future
                        and member_deadline > time.monotonic()
                    ):
                        # The leader's shorter budget cannot cancel another
                        # caller's fresh request. That caller can lead the next pair.
                        self._pending.setdefault(key, []).append((member, member_deadline))
                    else:
                        member.set_exception(error)
            raise
        finally:
            with self._condition:
                self._capturing.discard(key)
                self._remove_pending(key, request)
                self._condition.notify_all()
        if time.monotonic() >= deadline:
            raise TimeoutError("Worker inspection deadline expired")
        return future.result()

    def _remove_pending(self, key: FreshObservationKey, request: FreshObservationRequest) -> None:
        pending = self._pending.get(key)
        if pending is not None:
            if request in pending:
                pending.remove(request)
            if not pending:
                self._pending.pop(key, None)
