"""Chat routes (§9): send a message, run a /command, read the command catalog and
gateway availability."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import StreamingResponse

from planner.chat import service
from planner.chat.contracts import CommandCatalog
from planner.core import authctx
from planner.core.adapters.base import GatewayAdapter
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.errors import ErrorCode, PlannerError

router = APIRouter()

# The catalog is gateway-wide and near-static (it only changes when skills/commands
# change on disk), so it is memoized in app.state with a short TTL — cheap insurance
# against a child spawn per keystroke while still picking up a change within minutes.
_CATALOG_TTL_SECONDS = 300.0


def _cached_catalog(app: FastAPI, gateway: GatewayAdapter, refresh: bool) -> CommandCatalog:
    lock = app.state.chat_command_catalog_lock
    with lock:  # serialize concurrent misses so at most one child spawns
        entry = app.state.chat_command_catalog  # (CommandCatalog, expiry_monotonic) | None
        now = time.monotonic()
        if not refresh and entry is not None and entry[1] > now:
            return entry[0]  # type: ignore[no-any-return]
        catalog = service.catalog(gateway)  # a real child spawn happens here (non-test)
        app.state.chat_command_catalog = (catalog, now + _CATALOG_TTL_SECONDS)
        return catalog


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.post("/chat/{entity_id}/send")
async def send_message(
    entity_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    authctx.reject_agents(authctx.request_context(request))  # §11/§8: chat is human-only.
    text = body.get("text")
    if not isinstance(text, str):
        raise PlannerError(ErrorCode.validation, "text is required")
    clock: Clock = request.app.state.clock
    adapters: Adapters = request.app.state.adapters
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    conn = conn_factory()
    try:
        result = service.send(conn, adapters.gateway, entity_id, text, clock.now_unix())
    finally:
        conn.close()
    return asdict(result)


@router.get("/chat/{entity_id}/history")
async def chat_history(entity_id: str, request: Request) -> dict[str, Any]:
    authctx.reject_agents(authctx.request_context(request))  # §11/§8: chat is human-only.
    clock: Clock = request.app.state.clock
    adapters: Adapters = request.app.state.adapters
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    conn = conn_factory()
    try:
        result = service.history(conn, adapters.gateway, entity_id, clock.now_unix())
    finally:
        conn.close()
    return asdict(result)


@router.post("/chat/{entity_id}/stream")
async def stream_message(
    entity_id: str, body: dict[str, Any], request: Request
) -> StreamingResponse:
    authctx.reject_agents(authctx.request_context(request))  # §11/§8: chat is human-only.
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise PlannerError(ErrorCode.validation, "text is required")
    mode = body.get("mode", "message")
    if mode not in ("message", "command"):
        raise PlannerError(ErrorCode.validation, "mode must be message or command")
    clock: Clock = request.app.state.clock
    adapters: Adapters = request.app.state.adapters
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory

    def events() -> Iterator[str]:
        yield _sse("message_start", {"entity_id": entity_id, "mode": mode})
        conn = conn_factory()
        try:
            for chunk in service.stream(
                conn, adapters.gateway, entity_id, text, mode, clock.now_unix()
            ):
                if chunk.type == "token":
                    yield _sse("token", {"text": chunk.text})
                elif chunk.type == "done":
                    yield _sse(
                        "message_done",
                        {
                            "reply_text": chunk.reply_text,
                            "session_key": chunk.session_key,
                            "kind": chunk.kind,
                        },
                    )
                    break
        except PlannerError as exc:
            yield _sse("error", exc.to_payload()["error"])
        except Exception as exc:  # noqa: BLE001
            err = PlannerError(
                ErrorCode.gateway_offline,
                "gateway unavailable",
                {"cause": str(exc)},
            )
            yield _sse("error", err.to_payload()["error"])
        finally:
            conn.close()

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/chat/{entity_id}/command")
async def run_chat_command(
    entity_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    authctx.reject_agents(authctx.request_context(request))  # §11/§8: chat is human-only.
    command = body.get("command")
    if not isinstance(command, str) or not command.strip():
        raise PlannerError(ErrorCode.validation, "command is required")
    clock: Clock = request.app.state.clock
    adapters: Adapters = request.app.state.adapters
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    conn = conn_factory()
    try:
        result = service.run_command(
            conn, adapters.gateway, entity_id, command, clock.now_unix()
        )
    finally:
        conn.close()
    return asdict(result)


@router.get("/chat/commands")
async def chat_commands(request: Request) -> dict[str, Any]:
    # The gateway command/skill catalog for the "/" menu. Human-only, gateway-wide,
    # and cached (never a child spawn per request); ?refresh=1 busts the cache.
    authctx.reject_agents(authctx.request_context(request))
    adapters: Adapters = request.app.state.adapters
    refresh = request.query_params.get("refresh") == "1"
    catalog = _cached_catalog(request.app, adapters.gateway, refresh)
    return asdict(catalog)


@router.get("/chat/{entity_id}/status")
async def gateway_status(entity_id: str, request: Request) -> dict[str, Any]:
    # Most adapters are gateway-wide; routed production adapters can answer per entity.
    adapters: Adapters = request.app.state.adapters
    status_for_entity = getattr(adapters.gateway, "status_for_entity", None)
    if callable(status_for_entity):
        result = status_for_entity(entity_id)
    else:
        result = service.status(adapters.gateway)
    return {"available": result.available}
