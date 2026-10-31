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
from planner.scheduled_tickets.registry import build_participants
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
        self._stop_requested = False
        self._started = False

    def start(self, tick_seconds: int) -> None:
        """Admit polling only after publication startup has committed its epoch."""
        if self._started or self._stop_requested:
            raise RuntimeError("background loops cannot start twice or after stop")
        self._started = True
        for loop in (
            self.scheduled_ticket_loop,
            self.worker_step_readiness_loop,
            self.notification_loop,
            self.manager_wake_loop,
        ):
            if loop is not None:
                loop.start(tick_seconds)

        def wake_reconcilers() -> None:
            for loop in (
                self.worker_step_readiness_loop,
                self.notification_loop,
                self.manager_wake_loop,
            ):
                if loop is not None:
                    loop.wake()

        if self._lock_path is not None:
            self._stop_waking_on_change = change_signal.subscribe(wake_reconcilers)

    async def stop(self, *, deadline: float | None = None) -> None:
        """Stop listening, stop polling, let the steps in flight land, release the lock.

        The loop is stopped on a worker thread because its own drain waits on tasks that
        run on this event loop: stopping it inline would be the loop waiting for itself.
        """
        await asyncio.to_thread(self._stop_owned, deadline=deadline)

    def _stop_owned(self, *, deadline: float | None = None) -> None:
        """Drain every owner, retaining the lock and retry handles after any failure."""
        global _active
        if self._stopped:
            return
        self._stop_requested = True
        if deadline is None:
            deadline = _monotonic() + self._shutdown_grace_seconds
        failures: list[BaseException] = []
        if self._stop_waking_on_change is not None:
            try:
                self._stop_waking_on_change()
                self._stop_waking_on_change = None
            except BaseException as error:
                failures.append(error)
        for loop in (
            self.scheduled_ticket_loop,
            self.notification_loop,
            self.manager_wake_loop,
            self.worker_step_readiness_loop,
        ):
            if loop is not None:
                try:
                    loop.stop(deadline=deadline)
                except BaseException as error:
                    failures.append(error)
        if failures:
            for later in failures[1:]:
                failures[0].add_note(f"background cleanup also failed: {later!r}")
            raise failures[0]
        # A failed stop can still own a live scheduler or source runtime. Only
        # successful drains permit another owner to take the polling lock.
        if self._lock_path is not None:
            release_machine_lock(self._lock_path)
            self._lock_path = None
        self._stopped = True
        if _active is self:
            _active = None


_active: BackgroundLoops | None = None


def prepare_background_loops(
    config: Config,
    clock: Clock,
    *,
    conversation_system: ConversationSystem,
    asyncio_loop: asyncio.AbstractEventLoop,
    before_readiness_poll: Callable[[], bool] | None = None,
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
        _LOGGER.info("Background scheduling and dispatch disabled (dispatch_enabled=false)")
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info("Background loops not started: another process holds the polling lock")
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
                busy_timeout_ms=config.db_busy_timeout_ms,
                participants=build_participants(
                    config, clock,
                    workspace_admission=getattr(conversation_system, "workspace_admission", None),
                ),
                workspace_admission=getattr(conversation_system, "workspace_admission", None),
            )
            candidate_loop = WorkerStepReadinessLoop(
                config.db_path,
                clock,
                conversation_system=conversation_system,
                asyncio_loop=asyncio_loop,
                busy_timeout_ms=config.db_busy_timeout_ms,
                before_poll=before_readiness_poll,
            )
            candidate_notification_loop = NotificationLoop(
                config.db_path,
                clock,
                canonical_origin=config.trusted_ingress_canonical_origin,
                busy_timeout_ms=config.db_busy_timeout_ms,
                workspace_admission=getattr(conversation_system, "workspace_admission", None),
            )
            candidate_manager_wake_loop = ManagerWakeLoop(
                config.db_path,
                clock,
                conversation_system=conversation_system,
                asyncio_loop=asyncio_loop,
                busy_timeout_ms=config.db_busy_timeout_ms,
                workspace_admission=getattr(conversation_system, "workspace_admission", None),
            )
            candidate_notification_loop.reconcile_imported_state()
        except BaseException as startup_error:
            _LOGGER.exception("Background loops failed to start")
            # Keep the actual owner reachable if any partial cleanup fails. An
            # inert replacement would forget live work while retaining its fd.
            partial = BackgroundLoops(
                candidate_loop,
                config.dispatcher_lock_path,
                candidate_unsubscribe,
                shutdown_grace_seconds=float(config.shutdown_grace_seconds),
                scheduled_ticket_loop=candidate_schedule_loop,
                notification_loop=candidate_notification_loop,
                manager_wake_loop=candidate_manager_wake_loop,
            )
            _active = partial
            try:
                partial._stop_owned()
            except BaseException as cleanup_error:
                startup_error.add_note(f"background rollback also failed: {cleanup_error!r}")
                for note in getattr(cleanup_error, "__notes__", ()):
                    startup_error.add_note(note)
                _LOGGER.exception("Partially prepared background loops failed to stop")
            raise
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


def start_background_loops(
    config: Config,
    clock: Clock,
    *,
    conversation_system: ConversationSystem,
    asyncio_loop: asyncio.AbstractEventLoop,
) -> BackgroundLoops:
    """Retain legacy best-effort admission for callers without publication.

    The HTTP server deliberately calls prepare/start separately: publication must
    fail closed when reconciliation fails. Legacy callers keep their inert result.
    """
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")
    loops: BackgroundLoops | None = None
    try:
        loops = prepare_background_loops(
            config,
            clock,
            conversation_system=conversation_system,
            asyncio_loop=asyncio_loop,
        )
        loops.start(config.tick_seconds)
    except BaseException as startup_error:
        _LOGGER.exception("Background loops failed to start")
        if loops is not None:
            # Preparation cleans up its own failure. Once it returned, partial
            # admission belongs to this legacy synchronous caller instead.
            rollback_failed = False
            try:
                loops._stop_owned()
            except BaseException as cleanup_error:
                startup_error.add_note(f"background rollback also failed: {cleanup_error!r}")
                for note in getattr(cleanup_error, "__notes__", ()):
                    startup_error.add_note(note)
                _LOGGER.exception("Partially started background loops failed to stop")
                rollback_failed = True
            if rollback_failed:
                raise
        elif _active is not None:
            # Preparation failed to drain its retained partial owner.
            raise
        if not isinstance(startup_error, Exception):
            raise
        loops = BackgroundLoops(shutdown_grace_seconds=float(config.shutdown_grace_seconds))
        _active = loops
    return loops
