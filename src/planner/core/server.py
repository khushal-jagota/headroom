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
import os
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from planner.chat.api import router as chat_router
from planner.chat.service import ChatTurnLifecycle
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.errors import ErrorCode, PlannerError
from planner.core.testmode import TestModeAcceptingEmployeeRevisionRunner, build_test_router
from planner.core.trusted_ingress import TrustedIngressMiddleware, trusted_ingress_config
from planner.core.ws import tail_events
from planner.days.api import router as days_router
from planner.files.api import router as files_router
from planner.projects.api import router as projects_router
from planner.runtime.automatic_employee_step_eligibility_wake import (
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.sprints.api import router as sprints_router
from planner.tickets.api import router as tickets_router
from planner.worker_context.contracts import WorkerContextService
from planner.worker_types.configuration import (
    PRODUCTION_WORKER_TYPE_REGISTRY,
    configured_worker_type_registry,
)

_log = logging.getLogger("planner.server")

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.not_found: 404,
    ErrorCode.stale_claim: 409,
    ErrorCode.already_running: 409,
    ErrorCode.gateway_offline: 503,
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WEB_DIST = _REPO_ROOT / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"
_ASSETS_DIR = _REPO_ROOT / "assets"
_STATIC_DIR = _REPO_ROOT / "static"


def svelte_index_html() -> str:
    if _WEB_INDEX.is_file():
        return _WEB_INDEX.read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Panels</title></head><body>"
        '<div id="app" data-svelte-app>'
        "web/dist is missing; run npm --prefix web run build."
        "</div></body></html>"
    )


def http_status_for(code: ErrorCode) -> int:
    return _STATUS_BY_CODE.get(code, 400)


def _build_role_gateways(
    *,
    hermes_python: Path,
    planner_home: Path,
    worker_role: str,
    environ: Mapping[str, str],
    worker_context: WorkerContextService | None = None,
) -> tuple[Any, Any]:
    """Build the two gateway children with explicit, preserved role environments."""
    from planner.minds.shared_gateway import SharedGateway

    base_env = dict(environ)
    worker_gateway = SharedGateway(
        hermes_python=hermes_python,
        home=planner_home,
        worker_role=worker_role,
        base_env={**base_env, "PLAN_ACTOR": "worker"},
        worker_context=worker_context,
    )
    chief_gateway = SharedGateway(
        hermes_python=hermes_python,
        home=planner_home,
        worker_role="panels-chief-of-staff",
        base_env={**base_env, "PLAN_ACTOR": "chief"},
    )
    return worker_gateway, chief_gateway


async def _stop_runtime_with_deadline(runtime: Any, deadline: float) -> None:
    await runtime.stop(deadline=deadline)


def _shutdown_gateway_with_deadline(gateway: Any, deadline: float) -> None:
    gateway.shutdown(deadline=deadline)


def _start_gateway_if_available(gateway: Any) -> None:
    start = getattr(gateway, "start", None)
    if start is not None:
        start()


def _recover_running_human_chat_turns(
    conn_factory: Callable[[], sqlite3.Connection],
    chat_turn_lifecycle: ChatTurnLifecycle,
) -> None:
    conn = conn_factory()
    try:
        rows = conn.execute(
            "SELECT chat_turns.entity_id, chat_turns.mode "
            "FROM chat_turns "
            "LEFT JOIN tickets ON tickets.id = chat_turns.entity_id "
            "WHERE chat_turns.status = 'running' "
            "AND chat_turns.origin = 'human' "
            "AND (tickets.id IS NULL OR tickets.ticket_status <> 'agent_running_step') "
            "ORDER BY chat_turns.started_at, chat_turns.id"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        chat_turn_lifecycle.recover_human_turn(
            str(row["entity_id"]),
            str(row["mode"]),
        )


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
        # Build and validate the Worker-type registry once at startup so a malformed
        # definition refuses to boot loudly rather than failing on the first ticket op.
        # Importing the eagerly composed production registry validates shipped
        # definitions before the application begins serving.
        _ = PRODUCTION_WORKER_TYPE_REGISTRY
        # One integrity scan over the migrated tickets table: a corrupt live row
        # (unknown type, bad state/ceiling, malformed fields) fails boot loudly here,
        # after the registry is built and before any background loop touches a ticket.
        from planner.tickets import data as tickets_data

        audit_conn = conn_factory()
        try:
            tickets_data.audit_ticket_registry_integrity(audit_conn)
        finally:
            audit_conn.close()
        loops: Any = None
        shared_gateway: Any = None
        chat_gateway_to_shutdown: Any = None
        if config.test_mode and config.run_startup_recovery_in_test_mode:
            _recover_running_human_chat_turns(
                conn_factory,
                app_.state.chat_turn_lifecycle,
            )
        elif not config.test_mode:  # D6: background loops never run in test mode
            try:
                module = importlib.import_module("planner.core.loops")
                start = module.start_background_loops
                from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
                from planner.minds.config import (
                    provision_planner_home_skills,
                    resolve_hermes_python,
                    resolve_planner_home,
                )
                from planner.minds.shared_gateway import EntityRoutingGateway
                from planner.worker_context.service import SqliteWorkerContextService
            except (ImportError, AttributeError):
                _log.warning("planner.core.loops unavailable; running without background loops")
            else:
                planner_home_default = (
                    Path(config.db_path).expanduser().parent / "hermes-home"
                ).resolve(strict=False)
                planner_home = resolve_planner_home(default=planner_home_default)
                provision_planner_home_skills(planner_home)
                shared_gateway, chief_gateway = _build_role_gateways(
                    hermes_python=resolve_hermes_python(),
                    planner_home=planner_home,
                    worker_role=config.worker_skill,
                    environ=os.environ,
                    worker_context=SqliteWorkerContextService(conn_factory),
                )
                chat_gateway = EntityRoutingGateway(
                    shared_gateway,
                    {CHIEF_OF_STAFF_ENTITY_ID: chief_gateway},
                )
                chat_gateway_to_shutdown = chat_gateway
                app_.state.shared_gateway = shared_gateway
                app_.state.adapters = Adapters(gateway=chat_gateway)
                _start_gateway_if_available(shared_gateway)
                _start_gateway_if_available(chief_gateway)
                _recover_running_human_chat_turns(
                    conn_factory,
                    app_.state.chat_turn_lifecycle,
                )
                try:
                    loops = start(
                        config,
                        clock,
                        shared_gateway=shared_gateway,
                    )
                except Exception:
                    _log.exception(
                        "employee runtime composition failed; direct revisions unavailable"
                    )
                else:
                    app_.state.employee_step_runner = loops.employee_step_runner
                    app_.state.automatic_employee_step_eligibility_wake = (
                        loops.automatic_employee_step_eligibility_wake
                    )
        try:
            yield
        finally:
            deadline = _monotonic() + float(config.shutdown_grace_seconds)
            if loops is not None:
                await _stop_runtime_with_deadline(loops, deadline)
            if chat_gateway_to_shutdown is not None:
                _shutdown_gateway_with_deadline(chat_gateway_to_shutdown, deadline)
            elif shared_gateway is not None:
                _shutdown_gateway_with_deadline(shared_gateway, deadline)

    app = FastAPI(title="planner", version="2.0.0", lifespan=_lifespan)
    app.add_middleware(
        TrustedIngressMiddleware,
        config=trusted_ingress_config(config),
    )
    app.state.config = config
    app.state.clock = clock
    app.state.adapters = adapters
    app.state.conn_factory = conn_factory
    app.state.chat_turn_lifecycle = ChatTurnLifecycle(
        conn_factory,
        gateway_provider=lambda: app.state.adapters.gateway,
        now=clock.now_unix,
        db_path=config.db_path,
    )
    app.state.automatic_employee_step_eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()
    app.state.employee_step_runner = (
        TestModeAcceptingEmployeeRevisionRunner() if config.test_mode else None
    )
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
        projects_router,
        sprints_router,
        days_router,
        chat_router,
    ):
        app.include_router(domain_router, prefix="/api")

    app.include_router(files_router)

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        return {
            "ui_debounce_ms": config.ui_debounce_ms,
            "ws_poll_ms": config.ws_poll_ms,
            "test_mode": config.test_mode,
        }

    @app.get("/api/worker-types")
    async def worker_types() -> dict[str, Any]:
        # The single source of stage order / labels / gates / fields / ceiling range
        # per registered type, served from the ACTIVE registry (a test-installed probe
        # registry in-process). One entry per internal definition id.
        registry = configured_worker_type_registry()
        return {
            "worker_types": [
                registry.manifest(worker_type) for worker_type in registry.registered_worker_types()
            ]
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
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app
