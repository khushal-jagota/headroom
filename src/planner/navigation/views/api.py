"""Authenticated scoped navigation doors with the existing send fence."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import Response, StreamingResponse

from planner.core.authctx import RequestContext, request_context
from planner.core.response_compression import answers_with_an_event_stream
from planner.navigation.api import DeadlineStreamingResponse, FencedJSONResponse
from planner.navigation.delivery_authority import DeliveryUnavailable
from planner.navigation.views.contracts import ViewRequest
from planner.navigation.views.logic import compact
from planner.navigation.views.read import request

if TYPE_CHECKING:
    from planner.navigation.views.delivery import ScopedDeliveryRuntime


def router(runtime: ScopedDeliveryRuntime) -> APIRouter:
    result = APIRouter()

    def parse(raw: str) -> ViewRequest:
        if len(raw.encode()) > 128 * 1024:
            raise HTTPException(413, "navigation view request is too large")
        try:
            return request(json.loads(raw), runtime.config)
        except (ValueError, TypeError, RecursionError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @result.get("/api/navigation/view/snapshot")
    async def snapshot(
        view: str,
        ctx: Annotated[RequestContext, Depends(request_context)],
        fence: str | None = None,
    ) -> Response:
        value = parse(view)
        if fence and len(fence.encode()) > runtime.parent.config.proof_request_bytes:
            raise HTTPException(413, "navigation fence is too large")
        try:
            proof = json.loads(fence) if fence else None
        except (ValueError, RecursionError) as exc:
            raise HTTPException(400, "invalid navigation fence") from exc
        try:
            content, access = await runtime.snapshot(value, proof)
        except DeliveryUnavailable as exc:
            raise HTTPException(503, "navigation view is unavailable") from exc
        # Encoding runs off the event loop, while the existing response owns send fencing.
        response = FencedJSONResponse(
            None,
            admission=runtime.parent.admission,
            read=access,
            headers={"Cache-Control": "no-store"},
        )
        response.body = await asyncio.to_thread(lambda: compact(content).encode())
        response.init_headers({"Cache-Control": "no-store"})
        return response

    @result.get("/api/navigation/view/events")
    @answers_with_an_event_stream
    async def events(
        view: str, ctx: Annotated[RequestContext, Depends(request_context)]
    ) -> Response:
        value = parse(view)
        try:
            captured = await runtime.capture()
        except DeliveryUnavailable as exc:
            raise HTTPException(503, "navigation view is unavailable") from exc
        return DeadlineStreamingResponse(
            runtime.stream(value, captured),
            media_type="text/event-stream",
            send_timeout=runtime.parent.config.send_timeout_seconds,
            admission=runtime.parent.admission,
            read=captured[1],
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @result.get("/api/navigation/view/content")
    async def content(
        kind: str,
        identity: str,
        version: str,
        ctx: Annotated[RequestContext, Depends(request_context)],
    ) -> Response:
        from planner.navigation.views.content import recap

        try:
            body = await asyncio.to_thread(recap, runtime.parent.path, kind, identity, version)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from exc

        # The single selected body has a separate lifetime from resident navigation.
        # Unicode slices keep each transfer below the byte budget, including four-byte scalars.
        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(body), runtime.config.content_chunk_bytes // 4):
                yield body[start : start + runtime.config.content_chunk_bytes // 4].encode()

        return StreamingResponse(
            chunks(),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store", "ETag": version},
        )

    return result
