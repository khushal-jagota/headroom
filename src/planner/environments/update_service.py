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
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore, _atomic_write
from planner.environments.logic.update_experience import effective_request, experience
from planner.environments.update_configuration import UPDATE_AVAILABILITY_REFRESH_SECONDS
from planner.environments.update_contracts import (
    SUPPORTED_UPDATE_SCHEMA_FLOOR,
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateMode,
    UpdateProvider,
    UpdateRequestStore,
    UpdateStatus,
)
from planner.environments.update_requests import CANCELLED_DETAIL, UpdateConflict, parse_release


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
        prepare_mac_update: Callable[[PublishedUpdate], None] | None = None,
        mac_ready_for_dispatch: Callable[[OwnerUpdateRequest], bool] | None = None,
        lifecycle_store: DeploymentLifecycleStore | None = None,
    ) -> None:
        self.provider, self.store, self.conversation = provider, store, conversation
        self._connection_factory = connection_factory
        self.current_app_sha = current_app_sha
        self._prepare_mac_update = prepare_mac_update
        self._mac_ready_for_dispatch = mac_ready_for_dispatch
        self.lifecycle_store = lifecycle_store
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
        if (
            self._availability_error is not None
            or available is None
            or available.manifest.app_sha == self.current_app_sha
        ):
            return None
        try:
            validate_release(available)
        except ValueError:
            return None
        return available

    def status(self) -> UpdateStatus:
        system = self.conversation()
        activity = system.workspace_activity() if system is not None else "unknown"
        available = self.refresh_available()
        request = self.store.read()
        lifecycle = self.lifecycle_store.read().lifecycle if self.lifecycle_store else None
        state, actions = experience(
            request, lifecycle, available, self.provider is not None,
            self.current_app_sha, datetime.fromtimestamp(self._now(), UTC),
        )
        effective = effective_request(request, lifecycle)
        return UpdateStatus(
            self.provider is not None, available, request, activity,
            self._availability_error or (
                effective.detail if effective is not None and effective.state == "failed"
                else lifecycle.detail if effective is None and lifecycle is not None else None
            ), state=state, allowed_actions=actions,
            phase=lifecycle.phase if lifecycle else None, app_sha=self.current_app_sha,
        )

    def request(
        self, release: PublishedUpdate, mode: UpdateMode, *, confirm_activity: bool
    ) -> OwnerUpdateRequest:
        if self.provider is None:
            raise UpdateConflict("Owner updates need operator configuration.")
        validate_release(release)
        if release.manifest.app_sha == self.current_app_sha:
            raise UpdateConflict("The selected publication is already installed.")
        system = self.conversation()
        activity = system.workspace_activity() if system is not None else "unknown"
        if mode == "now" and activity != "idle" and not confirm_activity:
            raise UpdateConflict("Active or unknown work requires explicit confirmation.")
        if self._prepare_mac_update is not None:
            self._prepare_mac_update(release)
        request = self.store.create(
            OwnerUpdateRequest(
                uuid4().hex,
                release,
                mode,
                "requested",
                datetime.now(UTC).isoformat(),
                confirm_activity=confirm_activity,
            )
        )
        return request

    def dispatch_pending(self) -> bool:
        self.refresh_available()
        request = self.store.read()
        if request is None or request.state != "requested" or self.provider is None:
            return False
        system = self.conversation()
        if request.mode == "idle" and (system is None or system.workspace_activity() != "idle"):
            return False
        if self._mac_ready_for_dispatch is not None and not self._mac_ready_for_dispatch(request):
            return False
        try:
            validate_release(request.release)
            # Persist before the external call. An uncertain response never replays dispatch.
            request = self.store.transition(
                request.request_id, frozenset({"requested"}), "installing"
            )
            self.provider.dispatch(request)
        except UpdateConflict:
            return False
        except (OSError, RuntimeError, ValueError):
            self.store.transition(
                request.request_id,
                frozenset({"requested", "installing"}),
                "failed",
                detail=(
                    "Update dispatch failed or its result is uncertain. "
                    "Inspect the repository runner before retry."
                ),
            )
            return False
        return True

    def cancel(self, request_id: str) -> OwnerUpdateRequest:
        return self.store.transition(
            request_id, frozenset({"requested"}), "failed", detail=CANCELLED_DETAIL
        )

    def promote(self, request_id: str, *, confirm_activity: bool) -> OwnerUpdateRequest:
        return self.store.promote(request_id, confirm_activity=confirm_activity)


def validate_release(release: PublishedUpdate) -> None:
    if release.manifest.runtime_contract is None:
        raise UpdateConflict("This publication lacks a runtime contract. Republish the app.")
    if release.schema_floor != SUPPORTED_UPDATE_SCHEMA_FLOOR:
        raise UpdateConflict("This publication requires an unsupported schema floor.")
    expiry = datetime.fromisoformat(release.expires_at.replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise UpdateConflict("The pinned publication expired. Select a retained publication.")
