"""Authorized navigation snapshot and SSE doors, bound to the runtime workspace."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from planner.core.authctx import RequestContext, request_context
from planner.core.response_compression import answers_with_an_event_stream
from planner.navigation.delivery_authority import (
    DeliveryAdmission,
    DeliveryRead,
    DeliveryUnavailable,
)

if TYPE_CHECKING:
    from planner.navigation.delivery import DeliveryRuntime


class DeadlineStreamingResponse(StreamingResponse):
    def __init__(
        self,
        *args: Any,
        send_timeout: float,
        admission: DeliveryAdmission | None = None,
        read: DeliveryRead | None = None,
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

        try:
            await super().stream_response(bounded_send)
        except TimeoutError:
            pass
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
        read: DeliveryRead | None,
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


def delivery_router(runtime: DeliveryRuntime) -> APIRouter:
    router = APIRouter()

    def require_ready() -> None:
        if not runtime.ready:
            raise HTTPException(503, "navigation publication is not ready")

    @router.get("/api/navigation/snapshot")
    async def snapshot_route(
        response: Response,
        ctx: Annotated[RequestContext, Depends(request_context)],
        fence: str | None = None,
    ) -> Response:
        require_ready()
        response.headers["Cache-Control"] = "no-store"
        proof: object = None
        if fence is not None:
            if len(fence.encode()) > runtime.config.proof_request_bytes:
                raise HTTPException(413, "navigation proof request is too large")
            try:
                proof = json.loads(fence)
            except (ValueError, RecursionError) as exc:
                raise HTTPException(400, "invalid navigation fence") from exc
        try:
            value, read = await runtime.current_snapshot(proof)
        except DeliveryUnavailable as exc:
            raise HTTPException(503, "navigation publication is not ready") from exc
        return FencedJSONResponse(
            value,
            admission=runtime.admission,
            read=read,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/navigation/events")
    @answers_with_an_event_stream
    async def events_route(
        request: Request,
        ctx: Annotated[RequestContext, Depends(request_context)],
        cursor: str | None = None,
    ) -> StreamingResponse:
        require_ready()
        try:
            read = runtime.admission.capture()
        except DeliveryUnavailable as exc:
            raise HTTPException(503, "navigation publication is not ready") from exc
        # EventSource reconnect's Last-Event-ID takes precedence over the initial URL.
        resume = request.headers.get("last-event-id") or cursor or ""
        return DeadlineStreamingResponse(
            runtime.stream(resume, read),
            media_type="text/event-stream",
            send_timeout=runtime.config.send_timeout_seconds,
            admission=runtime.admission,
            read=read,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
