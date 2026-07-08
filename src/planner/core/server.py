"""FastAPI app factory shell. Mounts the domain routers under /api, serves the
static assets and the app shell, exposes GET /api/meta and the event WebSocket,
and — only in test mode — the /api/test/* control endpoints. The resolution
engine, dispatcher, and real route bodies land in later stages; here the surface
and the wiring are fixed.

This module and the domain api.py files are the only places FastAPI/pydantic
appear."""

from __future__ import annotations

import importlib
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from planner.chat.api import router as chat_router
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.errors import ErrorCode, PlannerError
from planner.core.testmode import build_test_router
from planner.core.ws import tail_events
from planner.days.api import router as days_router
from planner.sprints.api import router as sprints_router
from planner.tickets.api import router as tickets_router

_log = logging.getLogger("planner.server")

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.not_found: 404,
    ErrorCode.stale_claim: 409,
    ErrorCode.already_running: 409,
    ErrorCode.gateway_offline: 503,
}

_WEB_DIST = Path("web/dist")
_WEB_INDEX = _WEB_DIST / "index.html"


def svelte_index_html() -> str:
    if _WEB_INDEX.is_file():
        return _WEB_INDEX.read_text(encoding="utf-8")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Panels</title></head><body>"
        "<div id=\"app\" data-svelte-app>"
        "web/dist is missing; run npm --prefix web run build."
        "</div></body></html>"
    )


def http_status_for(code: ErrorCode) -> int:
    return _STATUS_BY_CODE.get(code, 400)


def create_app(
    config: Config,
    clock: Clock,
    adapters: Adapters,
    conn_factory: Callable[[], sqlite3.Connection],
) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app_: FastAPI) -> AsyncIterator[None]:
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
        loops: Any = None
        shared_gateway: Any = None
        if not config.test_mode:  # D6: background loops never run in test mode
            try:
                module = importlib.import_module("planner.core.loops")
                start = module.start_background_loops
                from planner.minds.config import resolve_hermes_python, resolve_planner_home
                from planner.minds.shared_gateway import SharedGateway
            except (ImportError, AttributeError):
                _log.warning("planner.core.loops unavailable; running without background loops")
            else:
                shared_gateway = SharedGateway(
                    hermes_python=resolve_hermes_python(),
                    home=resolve_planner_home(),
                    worker_role=config.worker_skill,
                )
                app_.state.shared_gateway = shared_gateway
                app_.state.adapters = Adapters(gateway=shared_gateway)
                loops = start(
                    config,
                    clock,
                    shared_gateway=shared_gateway,
                )
                # Expose System A for the API poke seam (readiness-changing endpoints wake it).
                # None in test mode (loops never start) -> the poke is a null-guarded no-op.
                app_.state.system_a = loops.system_a
        try:
            yield
        finally:
            if loops is not None:
                await loops.stop()
            if shared_gateway is not None:
                shared_gateway.shutdown()

    app = FastAPI(title="planner", version="2.0.0", lifespan=_lifespan)
    app.state.config = config
    app.state.clock = clock
    app.state.adapters = adapters
    app.state.conn_factory = conn_factory
    app.state.system_a = None  # set by the lifespan when background loops start (non-test only)
    app.state.shared_gateway = None
    # GET /api/chat/commands TTL cache: (CommandCatalog, expiry_monotonic) | None, plus a
    # lock so concurrent cache misses spawn at most one gateway child (chat/api.py).
    app.state.chat_command_catalog = None
    app.state.chat_command_catalog_lock = threading.Lock()

    @app.exception_handler(PlannerError)
    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())

    for domain_router in (
        tickets_router,
        sprints_router,
        days_router,
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
        await tail_events(
            websocket, since, conn_factory, config.ws_poll_ms, config.events_read_limit
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return svelte_index_html()

    if config.test_mode:
        app.include_router(build_test_router(config, clock), prefix="/api")

    if _WEB_DIST.is_dir():
        app.mount("/_app", StaticFiles(directory=_WEB_DIST, html=True), name="vite_app")
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")
    app.mount("/static", StaticFiles(directory="static"), name="static")
    return app
