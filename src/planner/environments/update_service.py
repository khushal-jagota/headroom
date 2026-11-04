"""Owner intent, repository dispatch, and the final workspace admission boundary."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from planner.conversation.contracts import ConversationSystem
from planner.core import change_signal
from planner.core.errors import PlannerError
from planner.environments.deployment_lifecycle import (
    DeploymentLifecycle,
    DeploymentLifecycleStore,
    _atomic_write,
)
from planner.environments.logic.update_experience import (
    effective_request,
    experience,
    request_deadline,
    settlement,
)
from planner.environments.update_configuration import UPDATE_AVAILABILITY_REFRESH_SECONDS
from planner.environments.update_contracts import (
    SUPPORTED_UPDATE_SCHEMA_FLOOR,
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateMode,
    UpdateProvider,
    UpdateRequestState,
    UpdateRequestStore,
    UpdateStatus,
)
from planner.environments.update_requests import UpdateConflict, parse_release
from planner.managed_skills import skill_names
from planner.tickets.data import audit_ticket_registry_integrity
from planner.worker_types.configuration import build_worker_runtime_definitions
from planner.worker_types.store import read_definitions


class UpdateService:
    def __init__(
        self,
        provider: UpdateProvider | None,
        store: UpdateRequestStore,
        conversation: Callable[[], ConversationSystem | None],
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        current_app_sha: str | None = None,
        availability_path: Path | None = None,
        can_refresh: Callable[[], bool] = lambda: True,
        now: Callable[[], float] = time.time,
        lifecycle_store: DeploymentLifecycleStore | None = None,
        recovery_admission: Callable[[], bool] = lambda: False,
    ) -> None:
        self.provider, self.store, self.conversation = provider, store, conversation
        self._connection_factory = connection_factory
        self.current_app_sha = current_app_sha
        self.lifecycle_store = lifecycle_store
        self.recovery_admission = recovery_admission
        self._availability_path = availability_path
        self._can_refresh, self._now = can_refresh, now
        self._refresh_lock = threading.Lock()
        self._last_checked = 0.0
        self._available: PublishedUpdate | None = None
        self._availability_error: str | None = None
        if availability_path is not None and availability_path.is_file():
            try:
                saved = json.loads(availability_path.read_text())
                self._available = parse_release(saved["release"]) if saved["release"] else None
                self._last_checked = float(saved["checked_at"])
            except (OSError, ValueError, KeyError, TypeError):
                self._availability_error = (
                    "The saved publication receipt is unavailable. Retry later."
                )

    def refresh_available(self, *, force: bool = False) -> PublishedUpdate | None:
        if self.provider is None or not self._can_refresh():
            return self._offered_release()
        if not force and self._now() - self._last_checked < UPDATE_AVAILABILITY_REFRESH_SECONDS:
            return self._offered_release()
        if not self._refresh_lock.acquire(blocking=False):
            return self._offered_release()
        try:
            previous = self._offered_release(), self._availability_error
            self._last_checked = self._now()
            try:
                self._available = self.provider.available()
                self._availability_error = None
                if self._availability_path is not None:
                    _atomic_write(
                        self._availability_path,
                        {
                            "checked_at": self._last_checked,
                            "release": asdict(self._available)
                            if self._available is not None
                            else None,
                        },
                    )
            except (OSError, RuntimeError, ValueError):
                self._availability_error = (
                    "The repository update service is unavailable. Retry later."
                )
            offered = self._offered_release()
            if previous != (offered, self._availability_error):
                change_signal.emit()
            return offered
        finally:
            self._refresh_lock.release()

    def _offered_release(self) -> PublishedUpdate | None:
        available = self._available
        if self.recovery_admission() and available is None:
            recorded = self.store.read()
            if recorded is not None:
                available = recorded.release
        if (
            self._availability_error is not None
            or available is None
            or (available.manifest.app_sha == self.current_app_sha
                and not self.recovery_admission())
        ):
            return None
        try:
            validate_release(available)
        except ValueError:
            return None
        return available

    def _snapshot(self) -> tuple[OwnerUpdateRequest | None, DeploymentLifecycle | None]:
        lifecycle: DeploymentLifecycle | None = None

        def decide(request: OwnerUpdateRequest) -> tuple[UpdateRequestState, str] | None:
            nonlocal lifecycle
            lifecycle = self.lifecycle_store.read().lifecycle if self.lifecycle_store else None
            return settlement(
                request, lifecycle, self.current_app_sha, datetime.fromtimestamp(self._now(), UTC)
            )

        request = self.store.settle(decide)
        if request is None:
            lifecycle = self.lifecycle_store.read().lifecycle if self.lifecycle_store else None
        return request, lifecycle

    def reconcile(self) -> OwnerUpdateRequest | None:
        return self._snapshot()[0]

    def status(self) -> UpdateStatus:
        system = self.conversation()
        activity = system.workspace_activity() if system is not None else "unknown"
        available = self.refresh_available()
        request, lifecycle = self._snapshot()
        now = datetime.fromtimestamp(self._now(), UTC)
        state, actions = experience(
            request, lifecycle, available, self.provider is not None, self.current_app_sha, now
        )
        effective = effective_request(request, lifecycle)
        deadline = request_deadline(effective) if effective is not None else None
        return UpdateStatus(
            self.provider is not None, available, request, activity,
            self._availability_error or (
                effective.detail if effective is not None and effective.state == "failed"
                else lifecycle.detail if effective is None and lifecycle is not None else None
            ),
            state=state, allowed_actions=actions,
            phase=lifecycle.phase if lifecycle else None,
            app_sha=self.current_app_sha,
            deadline_at=deadline.isoformat() if deadline else None,
            restart_deadline_at=(deadline.isoformat() if deadline else
                                 lifecycle.expires_at.isoformat() if lifecycle else None),
        )

    def request(
        self, release: PublishedUpdate, mode: UpdateMode, *, confirm_activity: bool
    ) -> OwnerUpdateRequest:
        if self.provider is None:
            raise UpdateConflict("Owner updates need operator configuration.")
        validate_release(release)
        self.reconcile()
        if release.manifest.app_sha == self.current_app_sha and not self.recovery_admission():
            raise UpdateConflict("The selected publication is already installed.")
        system = self.conversation()
        activity = system.workspace_activity() if system is not None else "unknown"
        if mode == "now" and activity != "idle" and not confirm_activity:
            raise UpdateConflict("Active or unknown work requires explicit confirmation.")
        request = self.store.create(
            OwnerUpdateRequest(
                uuid4().hex,
                release,
                mode,
                "pending",
                datetime.fromtimestamp(self._now(), UTC).isoformat(),
                confirm_activity=confirm_activity,
            )
        )
        return request

    def dispatch_pending(self) -> bool:
        self.refresh_available()
        request = self.reconcile()
        if request is None or request.state != "pending" or self.provider is None:
            return False
        system = self.conversation()
        if request.mode == "idle" and (system is None or system.workspace_activity() != "idle"):
            return False
        try:
            validate_release(request.release)
            # Persist before the external call. An uncertain response never replays dispatch.
            request = self.store.transition(
                request.request_id, frozenset({"pending"}), "dispatched"
            )
            self.provider.dispatch(request)
        except UpdateConflict:
            return False
        except (OSError, RuntimeError, ValueError):
            self.store.transition(
                request.request_id,
                frozenset({"pending", "dispatched"}),
                "failed",
                detail=(
                    "Update dispatch failed or its result is uncertain. "
                    "Inspect the repository runner before retry."
                ),
            )
            return False
        return True

    def cancel(self, request_id: str) -> OwnerUpdateRequest:
        return self.store.transition(request_id, frozenset({"pending", "dispatched"}), "cancelled")

    def promote(self, request_id: str, *, confirm_activity: bool) -> OwnerUpdateRequest:
        self.reconcile()
        return self.store.promote(request_id, confirm_activity=confirm_activity)

    async def claim(
        self, request_id: str, *, deployment_id: str | None = None,
    ) -> OwnerUpdateRequest:
        request = self.reconcile()
        if request is None or request.request_id != request_id or request.state != "dispatched":
            raise UpdateConflict("The update was cancelled or application already began.")
        validate_release(request.release)
        system = self.conversation()
        recovery = self.recovery_admission()
        if system is None and not recovery:
            raise UpdateConflict("Workspace activity is unknown. Application did not begin.")
        claimed: OwnerUpdateRequest | None = None

        def claim() -> None:
            nonlocal claimed
            try:
                connection = self._connection_factory()
                try:
                    # Audit the stored definition and Tickets in one writer-locked snapshot.
                    # Never publish a temporary registry into the running application.
                    connection.execute("BEGIN IMMEDIATE")
                    definitions = build_worker_runtime_definitions(
                        worker_type_definitions=read_definitions(connection),
                        known_skills=skill_names(connection),
                    )
                    audit_ticket_registry_integrity(
                        connection, worker_type_registry=definitions.worker_type_registry
                    )
                    claimed = self.store.transition(
                        request_id, frozenset({"dispatched"}), "applying",
                        deployment_id=deployment_id or request_id,
                    )
                finally:
                    try:
                        if connection.in_transaction:
                            connection.rollback()
                    finally:
                        connection.close()
            except UpdateConflict:
                raise
            except (PlannerError, RuntimeError, ValueError, sqlite3.Error) as exc:
                detail = (
                    "Ticket integrity check refused the update before service shutdown. "
                    f"Resolve the saved Ticket or Worker type error before retry: {exc}"
                )
                self.store.transition(
                    request_id, frozenset({"dispatched"}), "failed", detail=detail
                )
                raise UpdateConflict(detail) from exc

        if recovery or (system is not None and system.workspace_admission.closed):
            # Startup never opened ordinary work, or a previous attempt already
            # closed its admission. Do not close the fresh startup barrier again.
            # The same writer-locked integrity audit applies in both cases.
            claim()
            admitted = True
        else:
            assert system is not None
            admitted = await system.run_with_workspace_admission(
                claim, require_idle=request.mode == "idle" or not request.confirm_activity
            )
        if not admitted:
            if request.mode == "now":
                self.store.transition(
                    request_id,
                    frozenset({"dispatched"}),
                    "failed",
                    detail=(
                        "Workspace activity changed. Confirm active work before "
                        "a new Update now request."
                    ),
                )
                raise UpdateConflict(
                    "Workspace activity changed. Explicit confirmation is required."
                )
            self.store.transition(
                request_id,
                frozenset({"dispatched"}),
                "pending",
                detail="Workspace activity changed. The update still waits for idle.",
            )
            raise UpdateConflict("Workspace activity changed. Application did not begin.")
        assert claimed is not None
        return claimed


def validate_release(release: PublishedUpdate) -> None:
    if release.manifest.runtime_contract is None:
        raise UpdateConflict("This publication lacks a runtime contract. Republish the app.")
    if release.schema_floor != SUPPORTED_UPDATE_SCHEMA_FLOOR:
        raise UpdateConflict("This publication requires an unsupported schema floor.")
    expiry = datetime.fromisoformat(release.expires_at.replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise UpdateConflict("The pinned publication expired. Select a retained publication.")
