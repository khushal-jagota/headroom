"""Injected delivery fences, without ownership of a domain's permit or policy.

The caller supplies authoritative permit checks. The local generation serializes
invalidation with send admission; no producer gate spans a network await. Bytes
from an already admitted send cannot be recalled. Invalidation cancels that send
and prevents subsequent admissions instead.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection

from starlette.types import Message, Send


class DeliveryUnavailable(RuntimeError):
    """A configured authority cannot authorize this delivery."""


@dataclass(frozen=True)
class DeliveryAuthority:
    """Rebuild callbacks; the opaque permit belongs to its producer.

    Composition must translate its domain's unavailable exception into
    DeliveryUnavailable. This seam does not import or reproduce domain types.
    """

    capture_permit: Callable[[], object]
    validate: Callable[[object], None]
    validate_snapshot: Callable[[Connection, object], None]


@dataclass(frozen=True)
class DeliveryRead:
    admission_identity: object
    generation: int
    permit: object
    authority: DeliveryAuthority
    profile_check: Callable[[], None] | None = None


@dataclass(frozen=True)
class DeliveryDegradedRead:
    admission_identity: object
    generation: int
    profile_check: Callable[[], None]


type DeliveryAccess = DeliveryRead | DeliveryDegradedRead | None


class DeliveryAdmission:
    def __init__(self) -> None:
        self._identity = object()
        self._lock = threading.Lock()
        self._generation = 0
        self._authority: DeliveryAuthority | None = None
        self._configured = False
        self._sends: dict[asyncio.Task[None], asyncio.AbstractEventLoop] = {}
        self._waiters: dict[asyncio.Event, asyncio.AbstractEventLoop] = {}

    @property
    def configured(self) -> bool:
        with self._lock:
            return self._configured

    def configure(self, authority: DeliveryAuthority | None) -> None:
        # None means a configured but absent attachment, never legacy fallback.
        with self._lock:
            self._configured = True
            self._authority = authority
            self._invalidate_locked()

    def _invalidate_locked(self) -> None:
        self._generation += 1
        for task, loop in tuple(self._sends.items()):
            loop.call_soon_threadsafe(task.cancel)
        for event, loop in tuple(self._waiters.items()):
            loop.call_soon_threadsafe(event.set)

    def invalidate(self) -> None:
        with self._lock:
            self._invalidate_locked()

    def watch(self, event: asyncio.Event, read: DeliveryAccess) -> Callable[[], None]:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._validate_locked(read)
            self._waiters[event] = loop

        def forget() -> None:
            with self._lock:
                self._waiters.pop(event, None)

        return forget

    def capture(self, profile_check: Callable[[], None] | None = None) -> DeliveryRead | None:
        with self._lock:
            if not self._configured:
                return None
            authority = self._authority
            generation = self._generation
        if authority is None:
            raise DeliveryUnavailable("navigation authority has no active attachment")
        read = DeliveryRead(
            self._identity, generation, authority.capture_permit(), authority, profile_check
        )
        self.validate(read)
        return read

    def capture_degraded(self, profile_check: Callable[[], None]) -> DeliveryDegradedRead:
        with self._lock:
            if not self._configured:
                raise DeliveryUnavailable("navigation knowledge authority is not configured")
            read = DeliveryDegradedRead(self._identity, self._generation, profile_check)
        self.validate(read)
        return read

    def validate(self, read: DeliveryAccess) -> None:
        if read is None:
            with self._lock:
                if self._configured:
                    raise DeliveryUnavailable("navigation authority changed")
            return
        # Never hold the local admission lock while a producer acquires its gate.
        if isinstance(read, DeliveryRead):
            read.authority.validate(read.permit)
        if read.profile_check is not None:
            read.profile_check()
        with self._lock:
            self._validate_locked(read)

    def _validate_locked(self, read: DeliveryAccess) -> None:
        if read is None:
            if self._configured:
                raise DeliveryUnavailable("navigation authority changed")
        elif (
            read.admission_identity is not self._identity
            or read.generation != self._generation
            or (isinstance(read, DeliveryRead) and read.authority is not self._authority)
        ):
            raise DeliveryUnavailable("navigation delivery generation changed")

    def validate_snapshot(self, connection: Connection, read: DeliveryAccess) -> None:
        if isinstance(read, DeliveryRead):
            # This callback only checks installation identity in the supplied
            # SQL snapshot. Public producer validation occurs after SQL closes,
            # so its gate cannot invert the producer's gate-before-SQL order.
            read.authority.validate_snapshot(connection, read.permit)
        with self._lock:
            self._validate_locked(read)

    async def send(self, read: DeliveryAccess, send: Send, message: Message) -> None:
        await asyncio.to_thread(self.validate, read)
        loop = asyncio.get_running_loop()

        async def deliver() -> None:
            await send(message)

        with self._lock:
            self._validate_locked(read)
            task = loop.create_task(deliver())
            self._sends[task] = loop
        try:
            # An invalidation after admission cancels this registered task. It
            # cannot undo bytes already accepted by the transport.
            await task
        finally:
            with self._lock:
                self._sends.pop(task, None)
