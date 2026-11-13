"""Prefetch, one owner press, and the server's part of installing an update.

The server stages the next release ahead of time. A press records an attempt and
answers at once; the rest runs in the background: wait for idle when asked, pause
work under the workspace admission barrier with the Ticket integrity audit, back up
the database if the release migrates it, then start the stdlib installer in its own
user unit. The installer stops this process, so it reports the outcome in the record.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from planner.conversation.contracts import ConversationSystem
from planner.core import change_signal
from planner.core.errors import PlannerError
from planner.environments.backup import create_database_backup
from planner.environments.logic.update_experience import instant, resolve, settle
from planner.environments.runtime_contracts import NodeRuntimeContract
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    UpdateConflict,
    utc_now,
)
from planner.environments.update_barrier import UpdateBarrier
from planner.environments.update_configuration import (
    MAC_UPDATE_REPORT_SECONDS,
    UPDATE_AVAILABILITY_REFRESH_SECONDS,
    UPDATE_HEALTH_TIMEOUT_SECONDS,
    UPDATE_INSTALL_DEADLINE_SECONDS,
)
from planner.environments.update_contracts import (
    SUPPORTED_UPDATE_SCHEMA_FLOOR,
    PublishedUpdate,
    UpdateAttempt,
    UpdateAttemptState,
    UpdateMode,
    UpdateProvider,
    UpdateStatus,
)
from planner.environments.update_install import (
    installer_command,
    require_exact_schema_frontier,
    schema_change_pending,
    write_installer,
)
from planner.environments.update_prefetch import stage_release, staged_app_path
from planner.managed_skills import skill_names
from planner.platform_runtime.selection import get_platform_adapter
from planner.tickets.data import audit_ticket_registry_integrity
from planner.worker_types.configuration import build_worker_runtime_definitions
from planner.worker_types.store import read_definitions

_LOG = logging.getLogger(__name__)

ACTIVITY_CHANGED_DETAIL = (
    "Work started before the update began. Confirm active work to update now."
)
UPDATE_DRAIN_SECONDS = 300
WAIT_DETAIL = (
    "Work still waits for a safe point after five minutes. "
    "Leave the update pending, cancel it, or choose Update now."
)
STOP_TIMEOUT_DETAIL = "Work did not stop safely in five minutes. The update remains paused."

RESTARTED_DETAIL = "Panels restarted before the update began, so nothing changed."


def launch_transient_unit(attempt_id: str, command: list[str]) -> None:
    get_platform_adapter().launch_installer(attempt_id, command)


class UpdateService:
    def __init__(
        self,
        provider: UpdateProvider | None,
        attempts: FileUpdateAttemptStore,
        conversation: Callable[[], ConversationSystem | None],
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        current_root: Path,
        database: Path,
        backup_directory: Path,
        current_app_sha: str | None,
        health_url: str,
        service_unit: str,
        unavailable_reason: str | None = None,
        update_barrier: UpdateBarrier | None = None,
        prepare_mac_update: Callable[[PublishedUpdate], None] = lambda release: None,
        mac_phase: Callable[[str], str | None] = lambda mac_attempt_id: None,
        mac_reported_at: Callable[[str], float | None] = lambda mac_attempt_id: None,
        mac_configured: Callable[[], bool] = lambda: False,
        launch: Callable[[str, list[str]], None] = launch_transient_unit,
        stage: Callable[[UpdateProvider, PublishedUpdate, Path], Path] = stage_release,
        can_refresh: Callable[[], bool] = lambda: True,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.provider, self.attempts, self.conversation = provider, attempts, conversation
        self._connection_factory = connection_factory
        self.current_root, self.database = current_root, database
        self.backup_directory = backup_directory
        self.current_app_sha = current_app_sha
        self._health_url = health_url
        self._service_unit = service_unit
        self._unavailable_reason = unavailable_reason
        self._update_barrier = update_barrier
        self._prepare_mac_update, self._mac_phase = prepare_mac_update, mac_phase
        self._mac_reported_at = mac_reported_at
        self._mac_configured = mac_configured
        self._launch, self._stage = launch, stage
        self._can_refresh, self._now = can_refresh, now
        self._prefetch_lock = threading.Lock()
        self._last_checked = 0.0
        self._ready: PublishedUpdate | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._advancing = False
        self._advance_task: asyncio.Task[None] | None = None
        self._installer_starting: tuple[str, float] | None = None
        # The attempt whose start closed this process's workspace admission.
        self._paused_attempt_id: str | None = None
        self._last_state: str | None = None
        self._tasks: set[concurrent.futures.Future[None]] = set()
        self._work_wake_lock = threading.Lock()
        self._waiting_for_work = False
        self._unsubscribe_work: Callable[[], None] | None = None

    def _on_owner[T](self, action: Callable[[], T]) -> T:
        """Serialize state decisions and admission changes with the update coroutine."""
        owner = self._event_loop
        if owner is None or owner.is_closed() or not owner.is_running():
            return action()
        try:
            if asyncio.get_running_loop() is owner:
                return action()
        except RuntimeError:
            pass

        async def perform() -> T:
            return action()

        pending = perform()
        try:
            future = asyncio.run_coroutine_threadsafe(pending, owner)
        except RuntimeError as exc:
            pending.close()
            raise UpdateConflict(
                "Update controller stopped. Check update status before retrying."
            ) from exc
        try:
            return future.result(timeout=5)
        except TimeoutError as exc:
            future.cancel()
            raise UpdateConflict(
                "Update controller did not answer. Check update status before retrying."
            ) from exc

    def owns_preparation(self, attempt_id: str) -> bool:
        """Exclude this process's real snapshot and bounded installer launch handshake."""
        with self._work_wake_lock:
            task = self._advance_task
            starting = self._installer_starting
            return bool(
                (self._paused_attempt_id == attempt_id and task is not None and not task.done())
                or (starting is not None and starting[0] == attempt_id
                    and self._now() < starting[1])
            )

    def installer_observed(self, attempt_id: str) -> None:
        with self._work_wake_lock:
            if self._installer_starting is not None and self._installer_starting[0] == attempt_id:
                self._installer_starting = None

    def recover_orphaned_attempt(self, attempt_id: str, generation: int) -> int | None:
        """The caller holds the installer lifetime lock until returned marker cleanup."""
        return self._on_owner(lambda: self._recover_orphaned_attempt(attempt_id, generation))

    def _recover_orphaned_attempt(self, attempt_id: str, generation: int) -> int | None:
        barrier = self._update_barrier
        current = self.attempts.read()
        identity = "" if current is None else current.attempt_id
        if (barrier is None or barrier.generation != generation or identity != attempt_id
                or self.owns_preparation(attempt_id)
                or self._paused_attempt_id not in {None, attempt_id}):
            return None
        if current is not None and current.state not in {"done", "failed"}:
            self.attempts.change(attempt_id, frozenset({current.state}), lambda record: replace(
                record, state="failed", detail=(
                    RESTARTED_DETAIL if record.state in {"waiting", "preparing"}
                    else "The previous update did not complete. Try again."
                ),
            ))
        released = barrier.reopen_if_current(generation)
        if released is not None:
            self.reopen_trial_work(attempt_id)
        return released

    def reopen_trial_work(self, attempt_id: str) -> None:
        """Reopen only the attempt this observer saw, never a later retry's work."""
        current = self.attempts.read()
        identity = "" if current is None else current.attempt_id
        if (identity != attempt_id or self._paused_attempt_id not in {None, attempt_id}
                or (self._update_barrier is not None and self._update_barrier.frozen)):
            return
        system = self.conversation()
        if system is not None:
            system.cancel_update()
        self._paused_attempt_id = None

    # --- prefetch ------------------------------------------------------------------

    def ready_release(self) -> PublishedUpdate | None:
        """The staged release, offered only while it is still installable."""
        ready = self._ready
        if ready is None or ready.manifest.app_sha == self.current_app_sha:
            return None
        try:
            validate_release(ready)
        except UpdateConflict:
            return None
        return ready

    def refresh_available(self, *, force: bool = False) -> None:
        """Start one background check and prefetch on the readiness-loop cadence."""
        if self._unavailable_reason is not None or self.provider is None or not self._can_refresh():
            return
        current = self.attempts.read()
        if current is not None and current.state not in {"done", "failed"}:
            # The staged release belongs to this attempt until it ends.
            return
        if not force and self._now() - self._last_checked < UPDATE_AVAILABILITY_REFRESH_SECONDS:
            return
        if not self._prefetch_lock.acquire(blocking=False):
            return
        self._last_checked = self._now()
        threading.Thread(
            target=self._prefetch_holding_lock, name="panels-update-prefetch", daemon=True
        ).start()

    def _prefetch_holding_lock(self) -> None:
        try:
            self.prefetch()
        except Exception:
            # A failed check or download is retried on the next cadence. It never
            # becomes an error card: the owner was not offered this release yet.
            _LOG.warning("Update prefetch did not complete; it retries later", exc_info=True)
        finally:
            self._prefetch_lock.release()

    def prefetch(self) -> None:
        if self._unavailable_reason is not None:
            return
        assert self.provider is not None
        release = self.provider.available()
        if release is None or release.manifest.app_sha == self.current_app_sha:
            return
        validate_release(release)
        if release == self._ready:
            return
        staged = self._stage(self.provider, release, self.current_root)
        if isinstance(release.manifest.runtime_contract, NodeRuntimeContract):
            require_exact_schema_frontier(self.database, staged)
        self._prepare_mac_update(release)
        self._ready = release
        change_signal.emit()

    # --- status --------------------------------------------------------------------

    def _settled(self) -> UpdateAttempt | None:
        attempt = self.attempts.read()
        if attempt is None:
            return None
        if self._update_barrier is not None and self._update_barrier.frozen:
            return attempt  # A deadline or matching SHA does not prove trial health.
        ended = settle(attempt, self.current_app_sha, datetime.fromtimestamp(self._now(), UTC))
        if ended is None:
            return attempt
        try:
            return self.attempts.change(
                attempt.attempt_id, frozenset({"installing"}),
                lambda current: replace(
                    current, state=ended.state, detail=ended.detail,
                    steps={**current.steps, "settled": utc_now()},
                ),
            )
        except UpdateConflict:
            return self.attempts.read()

    def status(self) -> UpdateStatus:
        """Run from a worker: actor decisions precede independent SQLite reads."""
        result = self._on_owner(lambda: self._status())
        system = self.conversation()
        if system is None:
            return result
        activity = system.workspace_activity()
        work = (system.update_waiting_work()
                if result.state in {"available", "waiting"} else ())
        detail = result.detail
        if result.state == "waiting" and detail is None:
            detail = "New work waits. Panels saves admitted work before the update."
        if work:
            names = ", ".join(work)
            if result.state == "available":
                detail = f"Update now stops this work: {names}."
            else:
                detail = (f"{detail or 'The update waits for a safe point.'} "
                          f"Work before the update: {names}.")
        return replace(result, activity=activity, detail=detail)

    def _status(self) -> UpdateStatus:
        if self._unavailable_reason is not None:
            return UpdateStatus(
                "failed", (), "unknown", None, None, self.current_app_sha,
                self._unavailable_reason, None,
            )
        self.refresh_available()
        attempt = self._settled()
        self._reopen_after_unstarted(attempt)
        available = self.ready_release()
        release = attempt.release if attempt is not None else None
        state, actions, detail, deadline_at = resolve(
            attempt, available, self.current_app_sha,
            mac_applies=bool(release is not None and release.mac is not None
                             and self._mac_configured()),
            mac_phase=(self._mac_phase(attempt.mac_attempt_id)
                       if attempt is not None and attempt.mac_attempt_id else None),
            mac_reported_at=self._mac_report_time(attempt),
            mac_report_seconds=MAC_UPDATE_REPORT_SECONDS,
            now=datetime.fromtimestamp(self._now(), UTC),
        )
        return UpdateStatus(
            state, actions, "unknown", available, attempt, self.current_app_sha, detail,
            deadline_at,
        )

    def _mac_report_time(self, attempt: UpdateAttempt | None) -> datetime | None:
        if attempt is None or attempt.mac_attempt_id is None:
            return None
        reported = self._mac_reported_at(attempt.mac_attempt_id)
        return None if reported is None else datetime.fromtimestamp(reported, UTC)

    def _reopen_after_unstarted(self, attempt: UpdateAttempt | None) -> None:
        """Open work again when the attempt that paused it ended while this process runs.

        The installer stops this process once it swaps the app. If this process is still
        here and the attempt ended (refused, unstarted, settled), nothing replaced it.
        """
        paused = self._paused_attempt_id
        if paused is None:
            return
        if attempt is not None and attempt.attempt_id == paused and attempt.state in {
            "waiting", "preparing", "installing"
        }:
            return
        system = self.conversation()
        if system is not None:
            system.cancel_update()
        self._paused_attempt_id = None
        self._release_barrier()
        change_signal.emit()

    def _release_barrier(self) -> None:
        barrier = self._update_barrier
        if barrier is None:
            return
        generation = barrier.reopen()
        try:
            owner = asyncio.get_running_loop()
        except RuntimeError:
            barrier.remove(generation)
            return
        future = owner.run_in_executor(None, barrier.remove, generation)

        def removed(result: asyncio.Future[None]) -> None:
            if not result.cancelled() and (error := result.exception()) is not None:
                _LOG.error("Update marker cleanup failed", exc_info=(type(error), error,
                                                                      error.__traceback__))

        future.add_done_callback(removed)

    def _owns_attempt(self, attempt_id: str, state: UpdateAttemptState) -> bool:
        current = self.attempts.read()
        return current is not None and current.attempt_id == attempt_id and current.state == state

    def _deadline(self) -> str:
        return (
            datetime.fromtimestamp(self._now(), UTC)
            + timedelta(seconds=UPDATE_INSTALL_DEADLINE_SECONDS)
        ).isoformat()

    def _drain_deadline(self) -> str:
        return (datetime.fromtimestamp(self._now(), UTC)
                + timedelta(seconds=UPDATE_DRAIN_SECONDS)).isoformat()

    def _remaining_drain(self, attempt: UpdateAttempt) -> float:
        if attempt.deadline_at is None:
            return float(UPDATE_DRAIN_SECONDS)
        return max(0.001, instant(attempt.deadline_at).timestamp() - self._now())

    # --- owner actions -------------------------------------------------------------

    def install(self, app_sha: str, mode: UpdateMode, *, confirm_activity: bool) -> UpdateAttempt:
        system = self.conversation()
        activity = (system.workspace_activity()
                    if system is not None and mode == "now" and not confirm_activity
                    else "unknown")
        return self._on_owner(
            lambda: self._install(
                app_sha, mode, confirm_activity=confirm_activity, activity=activity
            )
        )

    def _install(
        self, app_sha: str, mode: UpdateMode, *, confirm_activity: bool, activity: str,
    ) -> UpdateAttempt:
        if self._unavailable_reason is not None:
            raise UpdateConflict(self._unavailable_reason)
        release = self.ready_release()
        if release is None or release.manifest.app_sha != app_sha:
            raise UpdateConflict("This update is no longer ready. Refresh and try again.")
        if mode == "now" and activity != "idle" and not confirm_activity:
            raise UpdateConflict("Active or unknown work requires explicit confirmation.")
        now = utc_now()
        attempt = self.attempts.create(UpdateAttempt(
            attempt_id=uuid4().hex,
            from_sha=self.current_app_sha or "",
            to_sha=app_sha,
            mode=mode,
            confirm_activity=confirm_activity,
            state="waiting",
            created_at=now,
            updated_at=now,
            release=release,
            deadline_at=self._drain_deadline(),
            steps={"pressed": now},
            mac_attempt_id=uuid4().hex if release.mac is not None else None,
        ))
        self.schedule_advance()
        return attempt

    def cancel(self, attempt_id: str) -> None:
        return self._on_owner(lambda: self._cancel(attempt_id))

    def _cancel(self, attempt_id: str) -> None:
        # Invalidate the continuation before reopening starts or writes.
        self.attempts.remove(attempt_id, frozenset({"waiting", "preparing"}))
        system = self.conversation()
        if system is not None:
            system.cancel_update()
        self._paused_attempt_id = None
        self._release_barrier()
        self._wait_for_work(False)
        if self._advance_task is not None:
            self._advance_task.cancel()

    def update_now(self, attempt_id: str, *, confirm_activity: bool) -> UpdateAttempt:
        return self._on_owner(
            lambda: self._update_now(attempt_id, confirm_activity=confirm_activity)
        )

    def _update_now(self, attempt_id: str, *, confirm_activity: bool) -> UpdateAttempt:
        if self._unavailable_reason is not None:
            raise UpdateConflict(self._unavailable_reason)
        if not confirm_activity:
            raise UpdateConflict("Confirm active work before Update now.")
        attempt = self.attempts.change(
            attempt_id, frozenset({"waiting"}),
            lambda current: replace(
                current, mode="now", confirm_activity=True, detail=None,
                deadline_at=self._drain_deadline()
            ),
        )
        self.schedule_advance()
        return attempt

    def retry(self, attempt_id: str) -> UpdateAttempt:
        return self._on_owner(lambda: self._retry(attempt_id))

    def _retry(self, attempt_id: str) -> UpdateAttempt:
        """A new attempt for the same ready release; the press is its confirmation."""
        attempt = self.attempts.read()
        if attempt is None or attempt.attempt_id != attempt_id or attempt.state != "failed":
            raise UpdateConflict("The update changed or already moved on.")
        return self._install(
            attempt.to_sha, "now", confirm_activity=True, activity="unknown"
        )

    def retry_mac(self, attempt_id: str) -> UpdateAttempt:
        return self._on_owner(lambda: self._retry_mac(attempt_id))

    def _retry_mac(self, attempt_id: str) -> UpdateAttempt:
        """A fresh Mac target for the same release; the Mac controller sees it as new."""
        if self._unavailable_reason is not None:
            raise UpdateConflict(self._unavailable_reason)
        return self.attempts.change(
            attempt_id, frozenset({"done"}),
            lambda current: replace(
                current, mac_attempt_id=uuid4().hex,
                steps={**current.steps, "retry_mac": utc_now()},
            ),
        )

    # --- the background part -------------------------------------------------------

    def bind_owner(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """Bind control during health-only startup without admitting ordinary work."""
        with self._work_wake_lock:
            self._event_loop = event_loop

    def start(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """Begin owning updates on this event loop; a `preparing` record is from a dead process."""
        self.stop()
        if self._unavailable_reason is not None:
            return
        self.bind_owner(event_loop)
        system = self.conversation()
        if system is not None:
            self._unsubscribe_work = system.workspace_admission.subscribe_operations_finished(
                lambda: self._operations_finished(event_loop)
            )
        attempt = self.attempts.read()
        if attempt is not None and attempt.state == "preparing":
            try:
                self.attempts.change(
                    attempt.attempt_id, frozenset({"preparing"}),
                    lambda current: replace(current, state="failed", detail=RESTARTED_DETAIL),
                )
            except UpdateConflict:
                pass
        self.schedule_advance()

    def stop(self) -> None:
        """Invalidate copied completion callbacks before the workspace shuts down."""
        with self._work_wake_lock:
            self._event_loop = None
            self._waiting_for_work = False
        if self._unsubscribe_work is not None:
            self._unsubscribe_work()
            self._unsubscribe_work = None

    def _wait_for_work(self, waiting: bool) -> None:
        with self._work_wake_lock:
            self._waiting_for_work = waiting

    def _operations_finished(self, owner: asyncio.AbstractEventLoop) -> None:
        with self._work_wake_lock:
            if self._event_loop is not owner or not self._waiting_for_work:
                return
            self._waiting_for_work = False
        try:
            owner.call_soon_threadsafe(self._advance_after_operations, owner)
        except RuntimeError:
            # A callback already copied by an operation can outlive its owner.
            return

    def _advance_after_operations(self, owner: asyncio.AbstractEventLoop) -> None:
        with self._work_wake_lock:
            if self._event_loop is not owner:
                return
        self.schedule_advance()

    def schedule_advance(self) -> None:
        with self._work_wake_lock:
            owner = self._event_loop
            if owner is None or owner.is_closed():
                return
            future = asyncio.run_coroutine_threadsafe(self._advance_for_owner(owner), owner)
            self._tasks.add(future)
            future.add_done_callback(self._advance_finished)

    async def _advance_for_owner(self, owner: asyncio.AbstractEventLoop) -> None:
        with self._work_wake_lock:
            if self._event_loop is not owner:
                return
        await self.advance()

    def _advance_finished(self, future: concurrent.futures.Future[None]) -> None:
        self._tasks.discard(future)
        if not future.cancelled() and (error := future.exception()) is not None:
            _LOG.error(
                "Update admission check failed", exc_info=(type(error), error, error.__traceback__)
            )

    def poll(self) -> bool:
        """The readiness-loop hook. True holds back new worker steps for this pass.

        It also announces a card state that changed with time alone, such as a Mac
        that stayed silent past its bound, because no record write announces that.
        """
        status = self.status()
        if status.state != self._last_state:
            self._last_state = status.state
            change_signal.emit()
        attempt = status.attempt
        if attempt is None:
            return False
        if attempt.state in {"preparing", "installing"}:
            return True
        if attempt.state != "waiting":
            return False
        # Background reconcilers share this wake and can briefly hold admission.
        # Let the event-loop owner check idle and claim admission, even when this
        # thread samples that temporary work. Keep the readiness hold unchanged.
        self.schedule_advance()
        return True

    async def advance(self) -> None:
        """Begin the waiting attempt when it may begin. Runs on the server's event loop."""
        if self._advancing:
            return
        self._advancing = True
        self._advance_task = asyncio.current_task()
        try:
            await self._advance()
        finally:
            self._advance_task = None
            self._advancing = False

    async def _advance(self) -> None:
        if self._unavailable_reason is not None:
            return
        attempt = self.attempts.read()
        system = self.conversation()
        self._wait_for_work(False)
        if attempt is None or attempt.state != "waiting" or system is None:
            return
        attempt_id = attempt.attempt_id
        if attempt.detail and not (
            attempt.mode == "idle" and attempt.deadline_at is None and attempt.detail == WAIT_DETAIL
        ):
            return
        if (attempt.deadline_at is not None
                and datetime.fromtimestamp(self._now(), UTC) >= instant(attempt.deadline_at)):
            self.attempts.change(attempt_id, frozenset({"waiting"}),
                                 lambda current: replace(
                                     current, detail=(WAIT_DETAIL if current.mode == "idle"
                                                      else STOP_TIMEOUT_DETAIL), deadline_at=None))
            return
        if (attempt.mode == "now" and not attempt.confirm_activity
                and self._paused_attempt_id is None):
            activity = await asyncio.to_thread(system.workspace_activity)
            if self.attempts.read() != attempt:
                self.schedule_advance()
                return
            if activity != "idle":
                self._fail(attempt_id, frozenset({"waiting"}), ACTIVITY_CHANGED_DETAIL)
                return
        self._paused_attempt_id = attempt_id
        self._wait_for_work(True)
        try:
            # Enter preparation in this controller task. A separately queued coroutine
            # could close starts after Cancel reopened them but before cancellation lands.
            async with asyncio.timeout(self._remaining_drain(attempt)):
                safe = await system.prepare_update(
                    "idle" if not attempt.confirm_activity else attempt.mode
                )
        except TimeoutError:
            if not self._owns_attempt(attempt_id, "waiting"):
                return
            self.attempts.change(attempt_id, frozenset({"waiting"}),
                                 lambda current: replace(current, detail=STOP_TIMEOUT_DETAIL))
            return
        except Exception as exc:
            if not self._owns_attempt(attempt_id, "waiting"):
                return
            _LOG.warning("Update safe-point check stopped: %s", exc, exc_info=True)
            # Unknown retirement is not a failed install: retain the admission fence.
            detail = f"Work could not reach a safe point: {exc}. The update remains paused."
            self.attempts.change(attempt_id, frozenset({"waiting"}),
                                 lambda current: replace(current, detail=detail))
            return
        if not safe:
            return
        if not self._owns_attempt(attempt_id, "waiting"):
            return

        def pause_work() -> None:
            # Audit the stored definitions and Tickets in one writer-locked snapshot.
            # Never publish a temporary registry into the running application.
            connection = self._connection_factory()
            try:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    definitions = build_worker_runtime_definitions(
                        worker_type_definitions=read_definitions(connection),
                        known_skills=skill_names(connection),
                    )
                    audit_ticket_registry_integrity(
                        connection, worker_type_registry=definitions.worker_type_registry
                    )
                except (PlannerError, RuntimeError, ValueError, sqlite3.Error) as exc:
                    self._fail(attempt_id, frozenset({"waiting"}), (
                        "The Ticket integrity check refused the update before Panels stopped. "
                        f"Resolve the saved Ticket or Worker type error, then try again: {exc}"
                    ))
                    raise UpdateConflict("Ticket integrity refused the update.") from exc
                self.attempts.change(
                    attempt_id, frozenset({"waiting"}),
                    lambda current: replace(
                        current, state="preparing",
                        steps={**current.steps, "work_paused": utc_now()},
                    ),
                )
            finally:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

        try:
            pause_work()
        except UpdateConflict:
            self._wait_for_work(False)
            self._reopen_after_unstarted(self.attempts.read())
            return
        # Work stays paused from here until the installer stops this service, or until
        # the attempt ends while this process still runs (see _reopen_after_unstarted).
        self._wait_for_work(False)
        self._paused_attempt_id = attempt_id
        try:
            if self._update_barrier is not None:
                generation = self._update_barrier.close()
                await asyncio.to_thread(self._update_barrier.persist, attempt_id, generation)
                if not self._owns_attempt(attempt_id, "preparing"):
                    return
                await asyncio.wait_for(self._update_barrier.drain(), self._remaining_drain(attempt))
            if not self._owns_attempt(attempt_id, "preparing"):
                return
            # This owner-loop claim is the cancellation boundary, before any snapshot.
            self.attempts.change(attempt_id, frozenset({"preparing"}),
                                 lambda current: replace(current, state="installing"))
            await asyncio.to_thread(self._start_installer, attempt_id)
        except TimeoutError:
            if not self._owns_attempt(attempt_id, "preparing"):
                return
            # An accepted detached save still owns custody. Do not reopen or snapshot.
            self.attempts.change(attempt_id, frozenset({"preparing"}), lambda current: replace(
                current, state="waiting", detail=("Accepted result saves still wait. "
                                                 "Choose Update now to retry after they finish, "
                                                 "or cancel the update."),
            ))
        except Exception as exc:
            current = self.attempts.read()
            if (current is None or current.attempt_id != attempt_id
                    or current.state not in {"preparing", "installing"}):
                return
            _LOG.exception("Update %s did not start its installer", attempt_id)
            self._fail(
                attempt_id, frozenset({"preparing", "installing"}),
                f"The update could not start, so nothing changed: {exc}",
            )
            self._reopen_after_unstarted(self.attempts.read())

    def _start_installer(self, attempt_id: str) -> None:
        attempt = self.attempts.read()
        assert attempt is not None and attempt.attempt_id == attempt_id
        staged = staged_app_path(self.current_root, attempt.to_sha)
        if not staged.is_dir():
            raise OSError("the staged release is missing")
        backup: str | None = None
        if isinstance(attempt.release.manifest.runtime_contract, NodeRuntimeContract):
            # A backup never permits a Node release to skip its exact schema frontier.
            require_exact_schema_frontier(self.database, staged)
            schema_changes = False
        else:
            schema_changes = schema_change_pending(self.database, staged)
        if schema_changes or get_platform_adapter().update_requires_snapshot:
            backup = str(create_database_backup(
                self.database, self.backup_directory, attempt.from_sha
            ))
        installer = write_installer(self.database.parent)
        command = installer_command(
            installer,
            current_root=self.current_root,
            attempt_id=attempt_id,
            service=self._service_unit,
            health_url=self._health_url,
            health_timeout_seconds=UPDATE_HEALTH_TIMEOUT_SECONDS,
        )

        def installing(current: UpdateAttempt) -> UpdateAttempt:
            steps = dict(current.steps)
            if backup is not None:
                steps["backup"] = utc_now()
            steps["installing"] = utc_now()
            return replace(
                current, state="installing", backup=backup,
                deadline_at=self._deadline(), steps=steps,
            )

        self.attempts.change(attempt_id, frozenset({"installing"}), installing)
        self._launch(attempt_id, command)
        with self._work_wake_lock:
            self._installer_starting = (attempt_id, self._now() + 5)

    def _fail(
        self, attempt_id: str, expected: frozenset[UpdateAttemptState], detail: str
    ) -> None:
        self._wait_for_work(False)
        try:
            self.attempts.change(
                attempt_id, expected,
                lambda current: replace(current, state="failed", detail=detail),
            )
        except UpdateConflict:
            pass


def validate_release(release: PublishedUpdate) -> None:
    if release.manifest.runtime_contract is None:
        raise UpdateConflict("This publication lacks a runtime contract. Republish the app.")
    if release.schema_floor != SUPPORTED_UPDATE_SCHEMA_FLOOR:
        raise UpdateConflict("This publication requires an unsupported schema floor.")
    expiry = datetime.fromisoformat(release.expires_at.replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise UpdateConflict("The pinned publication expired. Select a retained publication.")
