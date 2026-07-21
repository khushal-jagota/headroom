"""Wire-ordered, bounded ingress for ACP session notifications."""

from __future__ import annotations

import asyncio
import contextlib
import json
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from acp.connection import StreamDirection, StreamEvent
from acp.schema import SessionNotification
from pydantic import ValidationError

from .backend_contracts import AcpConversationIngress
from .configuration import ACP_SESSION_UPDATE_INGRESS_MAX_ITEMS
from .wire_contracts import (
    PROTOCOL_UPDATE_REJECTED_STATUS,
    ProtocolUpdateRejectedPayload,
)

_SESSION_UPDATE_METHOD: Final = "session/update"
_LOAD_SESSION_METHOD: Final = "session/load"


class AcpOrderedIngressError(RuntimeError):
    """Base error for a child generation whose ordered ingress cannot continue."""


class AcpSessionUpdateIngressOverflow(AcpOrderedIngressError):
    def __init__(self, limit: int, rejected_notification: str) -> None:
        super().__init__(
            f"ACP session-update ingress limit {limit} exceeded by "
            f"{rejected_notification}"
        )
        self.limit = limit
        self.rejected_notification = rejected_notification


class AcpSessionUpdateIngressClosed(AcpOrderedIngressError):
    pass


class AcpSessionUpdateCallbackMismatch(AcpOrderedIngressError):
    pass


class AcpConversationIngressFailure(AcpOrderedIngressError):
    pass


@dataclass(slots=True)
class _ReservedSlot:
    ordinal: int
    fingerprint: str | None
    payload: SessionNotification | ProtocolUpdateRejectedPayload | None = None
    downstream: AcpConversationIngress | None = None


@dataclass(slots=True, eq=False)
class AcpResponseConsumptionEpoch:
    method: str
    private_ingress: AcpConversationIngress | None
    response_target: asyncio.Future[int]
    private_session_id: str | None = None
    request_id: object | None = None


FatalCallback = Callable[[BaseException], Awaitable[None] | None]


def _canonical_notification_fingerprint(notification: SessionNotification) -> str:
    payload = notification.model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude_unset=True
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _rejected_discriminator(params: Any) -> str:
    if not isinstance(params, dict):
        return "missing"
    update = params.get("update")
    if not isinstance(update, dict):
        return "missing"
    discriminator = update.get("sessionUpdate")
    if (
        not isinstance(discriminator, str)
        or not discriminator
        or discriminator != discriminator.strip()
    ):
        return "missing"
    if any(unicodedata.category(character) == "Cc" for character in discriminator):
        return "missing"
    return discriminator


def _validated_session_id(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    session_id = params.get("sessionId")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    return session_id


class OrderedAcpConversationIngress:
    """Reserve raw update positions, fulfill them from typed SDK callbacks, consume serially."""

    def __init__(
        self,
        downstream: AcpConversationIngress,
        *,
        max_items: int = ACP_SESSION_UPDATE_INGRESS_MAX_ITEMS,
        fatal_callback: FatalCallback | None = None,
    ) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        self._downstream = downstream
        self._max_items = max_items
        self._fatal_callback = fatal_callback
        self._slots: deque[_ReservedSlot] = deque()
        self._next_ordinal = 1
        self._last_consumed_ordinal = 0
        self._changed = asyncio.Event()
        self._consumer_task: asyncio.Task[None] | None = None
        self._closed = False
        self._fatal_error: BaseException | None = None
        self._closing_drain = False
        self._response_epochs: deque[AcpResponseConsumptionEpoch] = deque()
        self._compat_load_epoch: AcpResponseConsumptionEpoch | None = None

    @property
    def last_reserved_ordinal(self) -> int:
        return self._next_ordinal - 1

    @property
    def last_consumed_ordinal(self) -> int:
        return self._last_consumed_ordinal

    @property
    def fatal_error(self) -> BaseException | None:
        return self._fatal_error

    def start(self) -> None:
        if self._consumer_task is not None:
            raise RuntimeError("ordered ACP ingress is already started")
        self._consumer_task = asyncio.create_task(
            self._consume(), name="panels.acp.ordered-ingress"
        )

    def begin_load_epoch(self) -> None:
        if self._compat_load_epoch is not None:
            raise RuntimeError("an ACP load epoch is already active")
        self._compat_load_epoch = self.begin_response_consumption_epoch(
            _LOAD_SESSION_METHOD
        )

    def begin_response_consumption_epoch(
        self,
        method: str,
        private_ingress: AcpConversationIngress | None = None,
        private_session_id: str | None = None,
    ) -> AcpResponseConsumptionEpoch:
        if self._closed or self._fatal_error is not None:
            raise self._fatal_error or AcpSessionUpdateIngressClosed("ACP ingress is closed")
        if not method:
            raise ValueError("ACP response-consumption method must not be empty")
        if private_ingress is None and private_session_id is not None:
            raise ValueError("an ACP private session ID requires a private ingress")
        if private_ingress is not None and (
            private_session_id is None or not private_session_id.strip()
        ):
            raise ValueError("ACP private session ID must not be blank")
        if private_ingress is not None and any(
            epoch.private_ingress is not None for epoch in self._response_epochs
        ):
            raise RuntimeError("a private ACP response-consumption epoch is already active")
        token = AcpResponseConsumptionEpoch(
            method=method,
            private_ingress=private_ingress,
            response_target=asyncio.get_running_loop().create_future(),
            private_session_id=private_session_id,
        )
        self._response_epochs.append(token)
        return token

    def abort_load_epoch(self) -> None:
        token = self._compat_load_epoch
        self._compat_load_epoch = None
        if token is not None:
            self.abort_response_consumption_epoch(token)

    def abort_response_consumption_epoch(
        self, token: AcpResponseConsumptionEpoch
    ) -> None:
        with contextlib.suppress(ValueError):
            self._response_epochs.remove(token)
        if not token.response_target.done():
            token.response_target.cancel()

    async def finish_load_epoch(self) -> None:
        token = self._compat_load_epoch
        if token is None:
            raise RuntimeError("no ACP load epoch is active")
        try:
            await self.finish_response_consumption_epoch(token)
        finally:
            self._compat_load_epoch = None

    async def finish_response_consumption_epoch(
        self, token: AcpResponseConsumptionEpoch
    ) -> None:
        if token not in self._response_epochs:
            raise RuntimeError("ACP response-consumption epoch is not active")
        try:
            target = await token.response_target
            await self.wait_until_consumed(target)
        finally:
            with contextlib.suppress(ValueError):
                self._response_epochs.remove(token)

    async def wait_for_load_response_observed(self) -> int:
        token = self._compat_load_epoch
        if token is None:
            raise RuntimeError("no ACP load epoch is active")
        return await asyncio.shield(token.response_target)

    def observe_stream(self, event: StreamEvent) -> None:
        message = event.message
        if (
            event.direction is StreamDirection.OUTGOING
            and "method" in message
        ):
            method = message.get("method")
            for epoch in self._response_epochs:
                if epoch.method == method and epoch.request_id is None:
                    epoch.request_id = message.get("id")
                    return

        if (
            event.direction is StreamDirection.INCOMING
            and message.get("method") == _SESSION_UPDATE_METHOD
            and "id" not in message
        ):
            self._reserve(message.get("params"))
            return

        if (
            event.direction is StreamDirection.INCOMING
            and "method" not in message
        ):
            for epoch in self._response_epochs:
                if epoch.request_id is not None and message.get("id") == epoch.request_id:
                    if not epoch.response_target.done():
                        epoch.response_target.set_result(self.last_reserved_ordinal)
                    return

    def _reserve(self, params: Any) -> None:
        if self._closed or self._fatal_error is not None:
            return
        if len(self._slots) >= self._max_items:
            self._fail(
                AcpSessionUpdateIngressOverflow(
                    self._max_items, _rejected_discriminator(params)
                )
            )
            return

        ordinal = self._next_ordinal
        session_id = _validated_session_id(params)
        private_epoch = next(
            (
                epoch
                for epoch in self._response_epochs
                if epoch.private_ingress is not None
                and not epoch.response_target.done()
                and epoch.private_session_id == session_id
            ),
            None,
        )
        selected_downstream = (
            private_epoch.private_ingress
            if private_epoch is not None
            else self._downstream
        )
        try:
            notification = SessionNotification.model_validate(
                params,
                strict=True,
                by_alias=True,
                by_name=False,
            )
            if session_id is None:
                raise ValueError("ACP session update has no valid session ID")
        except (ValidationError, ValueError):
            discriminator = _rejected_discriminator(params)
            slot = _ReservedSlot(
                ordinal=ordinal,
                fingerprint=None,
                payload=ProtocolUpdateRejectedPayload(
                    rejected_session_update=discriminator,
                    reason="ACP session update did not match the pinned protocol",
                    status=PROTOCOL_UPDATE_REJECTED_STATUS,
                ),
                downstream=selected_downstream,
            )
        else:
            slot = _ReservedSlot(
                ordinal=ordinal,
                fingerprint=_canonical_notification_fingerprint(notification),
                downstream=selected_downstream,
            )
        self._slots.append(slot)
        self._next_ordinal += 1
        self._changed.set()

    def fulfill_typed(self, notification: SessionNotification) -> None:
        if self._closed:
            self._fail(AcpSessionUpdateIngressClosed("typed callback arrived after ingress closed"))
            return
        fingerprint = _canonical_notification_fingerprint(notification)
        for slot in self._slots:
            if slot.payload is None and slot.fingerprint == fingerprint:
                slot.payload = notification
                self._changed.set()
                return
        self._fail(
            AcpSessionUpdateCallbackMismatch(
                "typed ACP callback had no matching unfulfilled observer reservation"
            )
        )

    async def wait_until_consumed(self, target_ordinal: int) -> None:
        while self._last_consumed_ordinal < target_ordinal:
            if self._fatal_error is not None:
                raise self._fatal_error
            self._changed.clear()
            if self._last_consumed_ordinal >= target_ordinal:
                break
            await self._changed.wait()
        if self._fatal_error is not None:
            raise self._fatal_error

    async def wait_until_reserved_fulfilled(self) -> None:
        """Wait for typed callbacks for the raw-accepted prefix after overflow."""

        while any(slot.payload is None for slot in self._slots):
            if self._closed:
                raise self._fatal_error or AcpSessionUpdateIngressClosed(
                    "ACP ingress closed before accepted callbacks were fulfilled"
                )
            self._changed.clear()
            if not any(slot.payload is None for slot in self._slots):
                break
            await self._changed.wait()

    async def wait_until_accepted_drained(self) -> None:
        """Wait for the complete raw-accepted prefix to leave the serial sink."""

        while self._slots:
            if self._closed:
                raise self._fatal_error or AcpSessionUpdateIngressClosed(
                    "ACP ingress closed before accepted updates drained"
                )
            self._changed.clear()
            if not self._slots:
                break
            await self._changed.wait()

    async def _consume(self) -> None:
        while True:
            while self._slots and self._slots[0].payload is not None:
                slot = self._slots[0]
                assert slot.payload is not None
                try:
                    downstream = slot.downstream or self._downstream
                    await downstream(slot.payload)
                except asyncio.CancelledError:
                    raise
                except BaseException as error:
                    failure = AcpConversationIngressFailure(
                        "ACP conversation ingress rejected an ordered update"
                    )
                    failure.__cause__ = error
                    self._fail(failure)
                    self._slots.clear()
                    self._changed.set()
                    return
                self._last_consumed_ordinal = slot.ordinal
                self._slots.popleft()
                self._changed.set()
            if self._closed and not self._slots:
                return
            if self._closed and self._closing_drain:
                # The connection is already retired, so no unfulfilled callback
                # can still arrive. The terminal cause is visible to all waiters.
                self._slots.clear()
                self._changed.set()
                return
            self._changed.clear()
            if self._slots and self._slots[0].payload is not None:
                continue
            await self._changed.wait()

    def _fail(self, error: BaseException) -> None:
        if self._fatal_error is not None:
            return
        self._fatal_error = error
        for epoch in self._response_epochs:
            if not epoch.response_target.done():
                epoch.response_target.set_exception(error)
        self._changed.set()
        if self._fatal_callback is not None:
            result = self._fatal_callback(error)
            if result is not None:
                asyncio.create_task(
                    self._await_fatal_callback(result),
                    name="panels.acp.ingress-fatal",
                )

    @staticmethod
    async def _await_fatal_callback(result: Awaitable[None]) -> None:
        await result

    async def close(
        self,
        *,
        drain: bool = True,
        cause: BaseException | None = None,
    ) -> None:
        if self._closed:
            if self._consumer_task is not None:
                if not drain and not self._consumer_task.done():
                    self._consumer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._consumer_task
            return
        self.retire_without_wait(drain=drain, cause=cause)
        task = self._consumer_task
        if task is None:
            return
        if not drain:
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def retire_without_wait(
        self,
        *,
        drain: bool,
        cause: BaseException | None = None,
    ) -> None:
        """Publish terminal state and stop acceptance before any teardown await."""

        if not self._closed:
            terminal_cause = cause or AcpSessionUpdateIngressClosed("ACP ingress retired")
            self._fail(terminal_cause)
            self._closed = True
            self._closing_drain = drain
            self._changed.set()
        task = self._consumer_task
        if not drain and task is not None and not task.done():
            task.cancel()
