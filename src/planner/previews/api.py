"""Own-Ticket registration and tailnet-only application transport routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response

from planner.core.authctx import request_context
from planner.core.contracts import PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.trusted_ingress import require_preview_ingress, trusted_ingress_config
from planner.previews.contracts import PreviewError, PreviewRecord
from planner.previews.transport import PreviewTransport

router = APIRouter()


def _ticket(request: Request) -> str:
    principal = request_context(request).principal
    if principal.kind is not PrincipalKind.ticket:
        raise PlannerError(
            ErrorCode.agent_forbidden, "preview commands require your Ticket Worker identity", {}
        )
    return principal.id


def _result(record: PreviewRecord | None) -> dict[str, Any]:
    if record is None:
        return {"preview": None}
    return {**asdict(record), "base_path": record.base_path, "url": record.url}


@router.post("/api/previews/prepare")
async def prepare(request: Request) -> dict[str, Any]:
    return _result(await request.app.state.previews.prepare(_ticket(request)))


@router.post("/api/previews/register")
async def register(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    ticket_id = _ticket(request)
    port = body.get("port")
    if set(body) != {"port"} or type(port) is not int:
        raise PlannerError(ErrorCode.validation, "register requires one integer port", {})
    return _result(await request.app.state.previews.register(ticket_id, port))


@router.get("/api/previews")
async def show(request: Request) -> dict[str, Any]:
    return _result(await request.app.state.previews.show(_ticket(request)))


@router.delete("/api/previews")
async def stop(request: Request) -> dict[str, Any]:
    record = await request.app.state.previews.stop(_ticket(request))
    if record is not None:
        await request.app.state.preview_transport.revoke(record.generation)
    return _result(record)


@router.api_route(
    "/dev/tickets/{ticket_id}/{generation}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def application(request: Request, ticket_id: str, generation: str, path: str) -> Response:
    require_preview_ingress(dict(request.scope), trusted_ingress_config(request.app.state.config))
    return await cast(PreviewTransport, request.app.state.preview_transport).http(
        request, ticket_id, generation
    )


@router.websocket("/dev/tickets/{ticket_id}/{generation}/{path:path}")
async def application_socket(
    websocket: WebSocket, ticket_id: str, generation: str, path: str
) -> None:
    try:
        require_preview_ingress(
            dict(websocket.scope), trusted_ingress_config(websocket.app.state.config)
        )
    except (PlannerError, PreviewError) as exc:
        await websocket.close(code=1008, reason=str(exc)[:120])
        return
    await websocket.app.state.preview_transport.websocket(websocket, ticket_id, generation)


async def handle_preview_error(request: Request, exc: Exception) -> Response:
    import html

    assert isinstance(exc, PreviewError)
    if request.url.path.startswith("/dev/tickets/"):
        return HTMLResponse(
            "<!doctype html><title>Application preview unavailable</title>"
            "<h1>Application preview unavailable</h1><p>" + html.escape(str(exc)) + "</p>",
            status_code=exc.status_code,
        )
    return JSONResponse(
        {"error": {"code": "preview_unavailable", "message": str(exc), "detail": {}}},
        status_code=exc.status_code,
    )
