"""Single-owner reconciliation and delivery loop for notifications."""

from __future__ import annotations

import logging
import threading
from time import monotonic

from planner.core.clock import Clock
from planner.core.db import connect
from planner.notifications import data
from planner.notifications.web_push import StandardsWebPushAdapter, WebPushAdapter

_LOG = logging.getLogger(__name__)


class NotificationLoop:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        canonical_origin: str | None,
        busy_timeout_ms: int = 5000,
        adapter: WebPushAdapter | None = None,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._busy_timeout_ms = busy_timeout_ms
        self._subject = canonical_origin or "https://localhost"
        self._adapter = adapter or StandardsWebPushAdapter()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> int:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            data.queue_deliveries(conn, now)
            identity = data.read_web_push_identity(conn)
            deliveries = data.pending_deliveries(conn, now)
            for delivery in deliveries:
                result = self._adapter.send(
                    delivery.subscription,
                    delivery.intent,
                    identity,
                    subject=self._subject,
                )
                data.record_delivery_result(
                    conn,
                    delivery,
                    now=now,
                    delivered=result.delivered,
                    expired=result.expired,
                    error=result.error,
                )
            return len(deliveries)
        finally:
            conn.close()

    def start(self, interval: int) -> None:
        if self._thread is not None:
            raise RuntimeError("notification loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="notification-loop",
            daemon=True,
        )
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            timeout = 10.0 if deadline is None else max(0.0, deadline - monotonic())
            thread.join(timeout=timeout)
            self._thread = None

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("notification poll failed")
            self._wake.wait(interval)
            self._wake.clear()
