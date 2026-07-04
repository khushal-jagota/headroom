"""FastAPI app factory shell. Mounts the domain routers under /api, serves the
static assets and the app shell, exposes GET /api/meta and the event WebSocket,
and — only in test mode — the /api/test/* control endpoints. The resolution
engine, dispatcher, and real route bodies land in later stages; here the surface
and the wiring are fixed.

This module and the domain api.py files are the only places FastAPI/pydantic
appear."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from planner.chat.api import router as chat_router
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.errors import ErrorCode, PlannerError
from planner.days.api import router as days_router
from planner.dispatch.api import router as dispatch_router
from planner.seed.api import router as seed_router
from planner.sprints.api import router as sprints_router
from planner.tickets.api import router as tickets_router

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.not_found: 404,
    ErrorCode.stale_claim: 409,
    ErrorCode.gateway_offline: 503,
}

_SHELL = "<!doctype html><meta charset=utf-8><title>planner</title><div id=app></div>"


def http_status_for(code: ErrorCode) -> int:
    return _STATUS_BY_CODE.get(code, 400)


def create_app(
    config: Config,
    clock: Clock,
    adapters: Adapters,
    conn_factory: Callable[[], sqlite3.Connection],
) -> FastAPI:
    # The DDL carries the literal 200 title cap; the config value is what write
    # paths enforce. Guard that they agree (amendment 11).
    assert config.title_max_chars == 200, "title_max_chars must equal the DDL literal (200)"

    app = FastAPI(title="planner", version="2.0.0")
    app.state.config = config
    app.state.clock = clock
    app.state.adapters = adapters
    app.state.conn_factory = conn_factory

    @app.exception_handler(PlannerError)
    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())

    for domain_router in (
        tickets_router,
        sprints_router,
        days_router,
        dispatch_router,
        seed_router,
        chat_router,
    ):
        app.include_router(domain_router, prefix="/api")

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        return {
            "ui_debounce_ms": config.ui_debounce_ms,
            "ws_poll_ms": config.ws_poll_ms,
            "test_mode": config.test_mode,
        }

    @app.websocket("/api/events")
    async def events_ws(websocket: WebSocket, since: int = 0) -> None:
        # The event tailer lands in stage 4; the path and `since` query are the
        # contract. Accept and close immediately for now.
        await websocket.accept()
        await websocket.close()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _SHELL

    if config.test_mode:

        @app.post("/api/test/tick-boundary")
        async def tick_boundary() -> dict[str, Any]:
            raise NotImplementedError

        @app.post("/api/test/tick-dispatcher")
        async def tick_dispatcher() -> dict[str, Any]:
            raise NotImplementedError

        @app.post("/api/test/set-now")
        async def set_now(body: dict[str, Any]) -> dict[str, Any]:
            raise NotImplementedError

    app.mount("/assets", StaticFiles(directory="assets"), name="assets")
    return app
