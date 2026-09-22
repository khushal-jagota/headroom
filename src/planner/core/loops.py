"""The scheduled, readiness, and notification loops one process owns."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import monotonic as _monotonic

from planner.conversation.contracts import ConversationSystem
from planner.core import change_signal
from planner.core.clock import Clock
from planner.core.config import Config
from planner.manager_wakes.runtime import ManagerWakeLoop
from planner.notifications.runtime import NotificationLoop
from planner.runtime.lock import ensure_machine_lock, release_machine_lock
from planner.runtime.worker_step_readiness_loop import WorkerStepReadinessLoop
from planner.scheduled_tickets.runtime import ScheduledTicketLoop

_LOGGER = logging.getLogger(__name__)


class BackgroundLoops:
    """The background loops and their shared machine lock, stopped as one."""

    def __init__(
        self,
        worker_step_readiness_loop: WorkerStepReadinessLoop | None = None,
        lock_path: str | None = None,
        stop_waking_on_change: Callable[[], None] | None = None,
        shutdown_grace_seconds: float = 30.0,
        scheduled_ticket_loop: ScheduledTicketLoop | None = None,
        notification_loop: NotificationLoop | None = None,
        manager_wake_loop: ManagerWakeLoop | None = None,
    ) -> None:
        self.worker_step_readiness_loop = worker_step_readiness_loop
        self.scheduled_ticket_loop = scheduled_ticket_loop
        self.notification_loop = notification_loop
        self.manager_wake_loop = manager_wake_loop
        self._lock_path = lock_path
        self._stop_waking_on_change = stop_waking_on_change
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._stopped = False

    async def stop(self, *, deadline: float | None = None) -> None:
        """Stop listening, stop polling, let the steps in flight land, release the lock.

        The loop is stopped on a worker thread because its own drain waits on tasks that
        run on this event loop: stopping it inline would be the loop waiting for itself.
        """
        global _active
        if self._stopped:
            return
        self._stopped = True
        if self._stop_waking_on_change is not None:
            self._stop_waking_on_change()
            self._stop_waking_on_change = None
        if deadline is None:
            deadline = _monotonic() + self._shutdown_grace_seconds
        if self.scheduled_ticket_loop is not None:
            await asyncio.to_thread(
                self.scheduled_ticket_loop.stop,
                deadline=deadline,
            )
        if self.notification_loop is not None:
            await asyncio.to_thread(
                self.notification_loop.stop,
                deadline=deadline,
            )
        if self.manager_wake_loop is not None:
            await asyncio.to_thread(
                self.manager_wake_loop.stop,
                deadline=deadline,
            )
        if self.worker_step_readiness_loop is not None:
            await asyncio.to_thread(
                self.worker_step_readiness_loop.stop,
                deadline=deadline,
            )
        if self._lock_path is not None:
            release_machine_lock(self._lock_path)
        if _active is self:
            _active = None


_active: BackgroundLoops | None = None


def start_background_loops(
    config: Config,
    clock: Clock,
    *,
    conversation_system: ConversationSystem,
    asyncio_loop: asyncio.AbstractEventLoop,
) -> BackgroundLoops:
    """Own the background loops when this process holds the machine lock.

    The readiness loop subscribes to the change signal, so any committed write asks it
    to look again instead of waiting out its periodic timer. Over-waking costs a
    read-only re-run of readiness. The schedule loop remains clock-driven so each due
    local-minute slot is evaluated through its durable occurrence identity.
    """
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")

    worker_step_readiness_loop: WorkerStepReadinessLoop | None = None
    scheduled_ticket_loop: ScheduledTicketLoop | None = None
    notification_loop: NotificationLoop | None = None
    manager_wake_loop: ManagerWakeLoop | None = None
    lock_path: str | None = None
    stop_waking_on_change: Callable[[], None] | None = None

    if not config.dispatch_enabled:
        _LOGGER.info(
            "Background scheduling and dispatch disabled (dispatch_enabled=false)"
        )
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info(
            "Background loops not started: another process holds the polling lock"
        )
    else:
        candidate_loop: WorkerStepReadinessLoop | None = None
        candidate_schedule_loop: ScheduledTicketLoop | None = None
        candidate_notification_loop: NotificationLoop | None = None
        candidate_manager_wake_loop: ManagerWakeLoop | None = None
        candidate_unsubscribe: Callable[[], None] | None = None
        try:
            candidate_schedule_loop = ScheduledTicketLoop(
                config.db_path,
                clock,
                boundary_hour=config.boundary_hour,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            candidate_loop = WorkerStepReadinessLoop(
                config.db_path,
                clock,
                conversation_system=conversation_system,
                asyncio_loop=asyncio_loop,
                boundary_hour=config.boundary_hour,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            candidate_notification_loop = NotificationLoop(
                config.db_path,
                clock,
                canonical_origin=config.trusted_ingress_canonical_origin,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            candidate_manager_wake_loop = ManagerWakeLoop(
                config.db_path,
                clock,
                conversation_system=conversation_system,
                asyncio_loop=asyncio_loop,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            candidate_schedule_loop.start(config.tick_seconds)
            candidate_loop.start(config.tick_seconds)
            candidate_notification_loop.start(config.tick_seconds)
            candidate_manager_wake_loop.start(config.tick_seconds)

            def wake_reconcilers() -> None:
                candidate_loop.wake()
                candidate_notification_loop.wake()
                candidate_manager_wake_loop.wake()

            candidate_unsubscribe = change_signal.subscribe(wake_reconcilers)
        except Exception:
            _LOGGER.exception("Background loops failed to start")
            if candidate_unsubscribe is not None:
                candidate_unsubscribe()
            if candidate_loop is not None:
                try:
                    candidate_loop.stop()
                except Exception:
                    _LOGGER.exception(
                        "partially started worker-step readiness loop failed to stop"
                    )
            if candidate_schedule_loop is not None:
                try:
                    candidate_schedule_loop.stop()
                except Exception:
                    _LOGGER.exception(
                        "partially started scheduled Ticket loop failed to stop"
                    )
            if candidate_notification_loop is not None:
                try:
                    candidate_notification_loop.stop()
                except Exception:
                    _LOGGER.exception(
                        "partially started notification loop failed to stop"
                    )
            if candidate_manager_wake_loop is not None:
                try:
                    candidate_manager_wake_loop.stop()
                except Exception:
                    _LOGGER.exception(
                        "partially started manager wake loop failed to stop"
                    )
            release_machine_lock(config.dispatcher_lock_path)
        else:
            worker_step_readiness_loop = candidate_loop
            scheduled_ticket_loop = candidate_schedule_loop
            notification_loop = candidate_notification_loop
            manager_wake_loop = candidate_manager_wake_loop
            stop_waking_on_change = candidate_unsubscribe
            lock_path = config.dispatcher_lock_path

    loops = BackgroundLoops(
        worker_step_readiness_loop,
        lock_path,
        stop_waking_on_change,
        shutdown_grace_seconds=float(config.shutdown_grace_seconds),
        scheduled_ticket_loop=scheduled_ticket_loop,
        notification_loop=notification_loop,
        manager_wake_loop=manager_wake_loop,
    )
    _active = loops
    return loops
