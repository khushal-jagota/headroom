"""Shared authority and retention for scoped navigation delivery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import uuid
from pathlib import Path

from planner.conversation.contracts import (
    SignalAvailability,
    SignalPublicationSource,
    SignalReadPermit,
)
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.delivery_authority import (
    DeliveryAdmission,
    DeliveryRead,
    DeliveryUnavailable,
)
from planner.navigation.delivery_contracts import CurrentAuthority
from planner.navigation.retention import prune


def _read_connection(path: Path) -> sqlite3.Connection:
    # No schema opens, migrations, journal-mode changes, or live writable fallback.
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    return conn


class DeliveryRuntime:
    def __init__(self, path: str | Path, config: DeliveryConfig | None = None) -> None:
        self.path = Path(path)
        self.config = config or DeliveryConfig()
        self.ready = False
        self.admission = DeliveryAdmission()
        self.delivery_session_id = uuid.uuid4().hex
        self._source: SignalPublicationSource | None = None
        self._closing = asyncio.Event()
        self._retention_task: asyncio.Task[None] | None = None

    def bind_signal_source(self, source: SignalPublicationSource) -> None:
        if self._source is not None and self._source is not source:
            raise RuntimeError("a replaced Signal source requires a new delivery runtime")
        self._source = source

    def _require_source(self) -> SignalPublicationSource:
        if self._source is None:
            raise DeliveryUnavailable("navigation has no Signal knowledge source")
        return self._source

    def _availability(self) -> SignalAvailability:
        value = self._require_source().signal_availability()
        if (
            type(value.generation) is not int
            or value.generation < 0
            or value.state not in ("initializing", "current", "unavailable")
            or (value.reason is not None and not isinstance(value.reason, str))
            or (value.state == "current" and value.reason is not None)
        ):
            raise DeliveryUnavailable("invalid Signal availability profile")
        return value

    def _validate_profile(self, expected: SignalAvailability) -> None:
        if self._availability() != expected:
            raise DeliveryUnavailable("Signal availability profile changed")

    def current_read(self) -> tuple[DeliveryRead, CurrentAuthority]:
        availability = self._availability()
        if availability.state != "current":
            raise DeliveryUnavailable("Signal runtime knowledge is not current")
        read = self.admission.capture(lambda: self._validate_profile(availability))
        if read is None or not isinstance(read.permit, SignalReadPermit):
            raise DeliveryUnavailable("Signal current delivery requires an exact permit")
        return read, self._current_authority(read, availability)

    def _current_authority(
        self, read: DeliveryRead, availability: SignalAvailability
    ) -> CurrentAuthority:
        if not isinstance(read.permit, SignalReadPermit):
            raise DeliveryUnavailable("Signal current delivery requires an exact permit")
        permit = read.permit
        if (
            availability.state != "current"
            or type(permit.generation) is not int
            or permit.generation != availability.generation
            or not all(
                isinstance(identity, str) and identity
                for identity in (permit.scope, permit.runtime_epoch, permit.installation_id)
            )
        ):
            raise DeliveryUnavailable("Signal permit does not match availability")
        authority: CurrentAuthority = {
            "delivery_session_id": self.delivery_session_id,
            "availability": {
                "state": "current",
                "generation": str(availability.generation),
                "reason": None,
            },
            "permit": {
                "scope": permit.scope,
                "runtime_epoch": permit.runtime_epoch,
                "installation_id": permit.installation_id,
                "generation": str(permit.generation),
            },
        }
        return authority

    async def start(self) -> None:
        if self.ready:
            raise RuntimeError("navigation delivery already started")
        # Owner startup has already reconciled and committed a fresh epoch.
        await asyncio.to_thread(self._prune)
        self._closing.clear()
        self.ready = True
        self._retention_task = asyncio.create_task(self._retain(), name="navigation-retention")

    def _prune(self) -> None:
        from planner.core.db import commit_without_change_signal, connect

        with contextlib.closing(connect(str(self.path))) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prune(conn, self.config)
                commit_without_change_signal(conn)
            except BaseException:
                conn.rollback()
                raise

    async def _retain(self) -> None:
        while not self._closing.is_set():
            try:
                await asyncio.wait_for(self._closing.wait(), self.config.retention_seconds)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._prune)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "navigation retention failed; retry next interval"
                    )

    async def close(self) -> None:
        self.ready = False
        self.admission.invalidate()
        self._closing.set()
        if self._retention_task is not None:
            await self._retention_task
            self._retention_task = None


def create_delivery_runtime(
    path: str | Path,
    config: DeliveryConfig | None = None,
) -> DeliveryRuntime:
    return DeliveryRuntime(path, config)
