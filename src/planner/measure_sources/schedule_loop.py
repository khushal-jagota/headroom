"""The single-server polling loop for exact-time measure source collection."""

from __future__ import annotations

import logging
import threading
from contextlib import nullcontext
from time import monotonic

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.clock import Clock
from planner.core.db import connect
from planner.measure_sources.participants import SlotParticipant

_LOG = logging.getLogger(__name__)


class SourceScheduleLoop:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        busy_timeout_ms: int = 5000,
        participants: tuple[SlotParticipant, ...],
        workspace_admission: WorkspaceAdmission | None = None,
    ) -> None:
        self._participants = participants
        self._workspace_admission = workspace_admission
        self._db_path = db_path
        self._clock = clock
        self._busy_timeout_ms = busy_timeout_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> None:
        with (
            self._workspace_admission.operation()
            if self._workspace_admission
            else nullcontext(True) as admitted
        ):
            if not admitted:
                return
            conn = connect(self._db_path, self._busy_timeout_ms)
            try:
                local_now = self._clock.now()
                for participant in self._participants:
                    participant.poll(conn, local_now=local_now, now=int(local_now.timestamp()))
            finally:
                conn.close()

    def start(self, interval: int) -> None:
        if self._thread is not None:
            raise RuntimeError("measure source loop already started")
        for participant in self._participants:
            participant.start()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="measure-source-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        failures: list[BaseException] = []
        for participant in self._participants:
            try:
                participant.stop(deadline=deadline)
            except BaseException as error:
                failures.append(error)
        thread = self._thread
        if thread is not None:
            # Occurrence work is synchronous and bounded by SQLite's busy timeout.
            timeout = 10.0 if deadline is None else max(0.0, deadline - monotonic())
            try:
                thread.join(timeout=timeout)
                if thread.is_alive():
                    raise RuntimeError("schedule clock remains active; polling lock is retained")
                self._thread = None
            except BaseException as error:
                failures.append(error)
        if failures:
            for later in failures[1:]:
                failures[0].add_note(f"schedule cleanup also failed: {later!r}")
            raise failures[0]

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("measure source poll failed")
            self._stop.wait(interval)
