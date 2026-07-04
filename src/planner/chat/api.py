"""Chat routes (§9): send a message, read gateway availability."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request

from planner.chat import service
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.errors import ErrorCode, PlannerError

router = APIRouter()


@router.post("/chat/{entity_id}/send")
async def send_message(
    entity_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
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


@router.get("/chat/{entity_id}/status")
async def gateway_status(entity_id: str, request: Request) -> dict[str, Any]:
    # Availability is gateway-wide; {entity_id} keeps the per-entity route shape.
    adapters: Adapters = request.app.state.adapters
    result = service.status(adapters.gateway)
    return {"available": result.available}
