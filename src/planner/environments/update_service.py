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
from planner.environments.logic.update_experience import resolve, settle
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    UpdateConflict,
    utc_now,
)
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
        # The attempt whose start closed this process's workspace admission.
        self._paused_attempt_id: str | None = None
        self._last_state: str | None = None
        self._tasks: set[concurrent.futures.Future[None]] = set()

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
        if self.provider is None or not self._can_refresh():
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
        assert self.provider is not None
        release = self.provider.available()
        if release is None or release.manifest.app_sha == self.current_app_sha:
            return
        validate_release(release)
        if release == self._ready:
            return
        self._stage(self.provider, release, self.current_root)
        self._prepare_mac_update(release)
        self._ready = release
        change_signal.emit()

    # --- status --------------------------------------------------------------------

    def _settled(self) -> UpdateAttempt | None:
        attempt = self.attempts.read()
        if attempt is None:
            return None
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
        self.refresh_available()
        attempt = self._settled()
        self._reopen_after_unstarted(attempt)
        system = self.conversation()
        activity = system.workspace_activity() if system is not None else "unknown"
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
            state, actions, activity, available, attempt, self.current_app_sha, detail,
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
            "preparing", "installing"
        }:
            return
        self._paused_attempt_id = None
        system = self.conversation()
        if system is not None:
            system.workspace_admission.reopen()
            change_signal.emit()

    def _deadline(self) -> str:
        return (
            datetime.fromtimestamp(self._now(), UTC)
            + timedelta(seconds=UPDATE_INSTALL_DEADLINE_SECONDS)
        ).isoformat()

    # --- owner actions -------------------------------------------------------------

    def install(self, app_sha: str, mode: UpdateMode, *, confirm_activity: bool) -> UpdateAttempt:
        release = self.ready_release()
        if release is None or release.manifest.app_sha != app_sha:
            raise UpdateConflict("This update is no longer ready. Refresh and try again.")
        system = self.conversation()
        activity = system.workspace_activity() if system is not None else "unknown"
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
            deadline_at=self._deadline() if mode == "now" else None,
            steps={"pressed": now},
            mac_attempt_id=uuid4().hex if release.mac is not None else None,
        ))
        self.schedule_advance()
        return attempt

    def cancel(self, attempt_id: str) -> None:
        self.attempts.remove(attempt_id, frozenset({"waiting"}))

    def update_now(self, attempt_id: str, *, confirm_activity: bool) -> UpdateAttempt:
        if not confirm_activity:
            raise UpdateConflict("Confirm active work before Update now.")
        attempt = self.attempts.change(
            attempt_id, frozenset({"waiting"}),
            lambda current: replace(
                current, mode="now", confirm_activity=True, deadline_at=self._deadline()
            ),
        )
        self.schedule_advance()
        return attempt

    def retry(self, attempt_id: str) -> UpdateAttempt:
        """A new attempt for the same ready release; the press is its confirmation."""
        attempt = self.attempts.read()
        if attempt is None or attempt.attempt_id != attempt_id or attempt.state != "failed":
            raise UpdateConflict("The update changed or already moved on.")
        return self.install(attempt.to_sha, "now", confirm_activity=True)

    def retry_mac(self, attempt_id: str) -> UpdateAttempt:
        """A fresh Mac target for the same release; the Mac controller sees it as new."""
        return self.attempts.change(
            attempt_id, frozenset({"done"}),
            lambda current: replace(
                current, mac_attempt_id=uuid4().hex,
                steps={**current.steps, "retry_mac": utc_now()},
            ),
        )

    # --- the background part -------------------------------------------------------

    def start(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """Begin owning updates on this event loop; a `preparing` record is from a dead process."""
        self._event_loop = event_loop
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

    def schedule_advance(self) -> None:
        if self._event_loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.advance(), self._event_loop)
        self._tasks.add(future)
        future.add_done_callback(self._tasks.discard)

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
        system = self.conversation()
        if attempt.mode == "idle" and (system is None or system.workspace_activity() != "idle"):
            return False
        self.schedule_advance()
        return True

    async def advance(self) -> None:
        """Begin the waiting attempt when it may begin. Runs on the server's event loop."""
        if self._advancing:
            return
        self._advancing = True
        try:
            await self._advance()
        finally:
            self._advancing = False

    async def _advance(self) -> None:
        attempt = self.attempts.read()
        system = self.conversation()
        if attempt is None or attempt.state != "waiting" or system is None:
            return
        if attempt.mode == "idle" and system.workspace_activity() != "idle":
            return
        attempt_id = attempt.attempt_id

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
            admitted = await system.run_with_workspace_admission(
                pause_work, require_idle=attempt.mode == "idle" or not attempt.confirm_activity
            )
        except UpdateConflict:
            return
        if not admitted:
            if attempt.mode == "now":
                self._fail(attempt_id, frozenset({"waiting"}), ACTIVITY_CHANGED_DETAIL)
            return
        # Work stays paused from here until the installer stops this service, or until
        # the attempt ends while this process still runs (see _reopen_after_unstarted).
        self._paused_attempt_id = attempt_id
        try:
            await asyncio.to_thread(self._start_installer, attempt_id)
        except Exception as exc:
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
        if schema_change_pending(self.database, staged):
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

        self.attempts.change(attempt_id, frozenset({"preparing"}), installing)
        self._launch(attempt_id, command)

    def _fail(
        self, attempt_id: str, expected: frozenset[UpdateAttemptState], detail: str
    ) -> None:
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
