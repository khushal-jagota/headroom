"""Authorized navigation snapshot and SSE doors, bound to the runtime workspace."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Send

from planner.core.authctx import RequestContext, request_context
from planner.core.response_compression import answers_with_an_event_stream
from planner.navigation.delivery_contracts import SnapshotResponse

if TYPE_CHECKING:
    from planner.navigation.delivery import DeliveryRuntime


class DeadlineStreamingResponse(StreamingResponse):
    def __init__(self, *args: Any, send_timeout: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.send_timeout = send_timeout

    async def stream_response(self, send: Send) -> None:
        async def bounded_send(message: Any) -> None:
            await asyncio.wait_for(send(message), self.send_timeout)

        try:
            await super().stream_response(bounded_send)
        except TimeoutError:
            pass
        finally:
            # Closing the async iterator releases the signal and shutdown subscriptions.
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                await close()


def delivery_router(runtime: DeliveryRuntime) -> APIRouter:
    from planner.navigation.delivery import read_snapshot

    router = APIRouter()

    def require_ready() -> None:
        if not runtime.ready:
            raise HTTPException(503, "navigation publication is not ready")

    @router.get("/api/navigation/snapshot")
    async def snapshot_route(
        response: Response,
        ctx: Annotated[RequestContext, Depends(request_context)],
        fence: str | None = None,
    ) -> SnapshotResponse:
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
        return await asyncio.to_thread(read_snapshot, runtime.path, proof)

    @router.get("/api/navigation/events")
    @answers_with_an_event_stream
    async def events_route(
        request: Request,
        ctx: Annotated[RequestContext, Depends(request_context)],
        cursor: str | None = None,
    ) -> StreamingResponse:
        require_ready()
        # EventSource reconnect's Last-Event-ID takes precedence over the initial URL.
        resume = request.headers.get("last-event-id") or cursor or ""
        return DeadlineStreamingResponse(
            runtime.stream(resume),
            media_type="text/event-stream",
            send_timeout=runtime.config.send_timeout_seconds,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
