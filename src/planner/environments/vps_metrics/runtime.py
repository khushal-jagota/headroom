"""One sequential sampler per store, with stored-data readers in every process."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from planner.core.read_connections import ReadConnections

from .config import MetricsConfig
from .contracts import CollectionStatus, Collector, MetricsStore

log = logging.getLogger(__name__)


class MetricsRuntime:
    def __init__(
        self,
        path: Path,
        store: MetricsStore,
        collector: Collector | None,
        config: MetricsConfig,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.store = store
        self.collector = collector
        self.config = config
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.reads = ReadConnections(path, readers=config.read_workers, name="vps-read")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner = False
        self.failure: CollectionStatus | None = None

    @property
    def owns_collection(self) -> bool:
        return self._owner

    def start(self) -> None:
        try:
            self.store.initialize()
            if self.collector is not None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="vps-sampler")
                self._thread.start()
        except BaseException:
            self._stop.set()
            self.reads.close()
            raise

    def _acquire(self) -> IO[bytes] | None:
        handle = self.path.with_name(self.path.name + ".lock").open("a+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                handle = self._acquire()
                if handle is None:
                    self._stop.wait(self.config.ownership_retry_seconds)
                    continue
                self._owner = True
                try:
                    self._sample_loop()
                finally:
                    self._owner = False
                    handle.close()
            except Exception:
                log.exception("VPS sampler ownership failed")
                self._stop.wait(self.config.ownership_retry_seconds)

    def _sample_loop(self) -> None:
        assert self.collector is not None
        next_sample = next_slow = next_prune = 0.0
        was_active = False
        while not self._stop.is_set():
            now = self.wall_clock()
            tick = self.monotonic()
            cadence = self.config.idle_seconds
            try:
                active = self.store.activity_until() > now
                cadence = self.config.active_seconds if active else self.config.idle_seconds
                if active and not was_active:
                    next_sample = tick
                was_active = active
                if tick >= next_sample:
                    sample = self.collector.collect(include_slow=tick >= next_slow)
                    sample["expected_interval"] = cadence
                    self.store.append(sample)
                    self.failure = None
                    if tick >= next_slow:
                        next_slow = tick + self.config.slow_seconds
                    # Never replay missed ticks or overlap collections.
                    next_sample = self.monotonic() + cadence
                if tick >= next_prune:
                    self.store.prune(now)
                    next_prune = tick + self.config.prune_seconds
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                self.failure = {
                    "availability": "unavailable",
                    "reason": reason,
                    "last_sample_at": None,
                    "last_attempt_at": now,
                    "cadence_seconds": cadence,
                }
                try:
                    self.store.record_failure(now, reason, cadence)
                except sqlite3.Error:
                    # The process can still expose failure when a full disk prevents writes.
                    log.warning("VPS metrics failure could not be persisted: %s", reason)
                next_sample = self.monotonic() + cadence
            self._stop.wait(self.config.activity_check_seconds)

    async def stop(self, *, deadline: float) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            remaining = max(0.0, deadline - time.monotonic())
            await asyncio.to_thread(thread.join, remaining)
            if thread.is_alive():
                # Keep the ownership descriptor with the active collector until it exits.
                log.warning("VPS sampler exceeded the shutdown deadline")
        # Closure continues off-loop if an already bounded read uses the last grace time.
        closing = asyncio.get_running_loop().run_in_executor(None, self.reads.close)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            await asyncio.wait_for(asyncio.shield(closing), timeout=remaining)
        except TimeoutError:
            log.warning("VPS readers exceeded the shutdown deadline")
