"""Authorized navigation snapshot and SSE doors, bound to the runtime workspace."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from planner.navigation.delivery_authority import (
    DeliveryAccess,
    DeliveryAdmission,
    DeliveryUnavailable,
)

if TYPE_CHECKING:
    pass


class DeadlineStreamingResponse(StreamingResponse):
    def __init__(
        self,
        *args: Any,
        send_timeout: float,
        admission: DeliveryAdmission | None = None,
        read: DeliveryAccess = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.send_timeout = send_timeout
        self.admission = admission
        self.read = read

    async def stream_response(self, send: Send) -> None:
        started = False

        async def bounded_send(message: Any) -> None:
            nonlocal started
            operation = (
                send(message)
                if self.admission is None
                else self.admission.send(self.read, send, message)
            )
            await asyncio.wait_for(operation, self.send_timeout)
            if message["type"] == "http.response.start":
                started = True

        # A timeout must reach the server so it closes the failed transport.
        # Silence hid an existing send failure and left the response incomplete.
        try:
            await super().stream_response(bounded_send)
        except DeliveryUnavailable:
            if not started:
                response = JSONResponse(
                    {"detail": "navigation publication is not ready"},
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
                await send(
                    {"type": "http.response.start", "status": 503, "headers": response.raw_headers}
                )
                await send({"type": "http.response.body", "body": response.body})
            else:
                await asyncio.wait_for(
                    send({"type": "http.response.body", "body": b"", "more_body": False}),
                    self.send_timeout,
                )
        finally:
            # Closing the async iterator releases the signal and shutdown subscriptions.
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                await close()


class FencedJSONResponse(JSONResponse):
    def __init__(
        self,
        content: object,
        *,
        admission: DeliveryAdmission,
        read: DeliveryAccess,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, headers=headers)
        self.admission = admission
        self.read = read

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def fenced_send(message: Message) -> None:
            nonlocal started
            await self.admission.send(self.read, send, message)
            if message["type"] == "http.response.start":
                started = True

        try:
            await super().__call__(scope, receive, fenced_send)
        except DeliveryUnavailable:
            if started:
                raise
            await JSONResponse(
                {"detail": "navigation publication is not ready"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
