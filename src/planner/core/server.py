"""FastAPI application composition for the ACP-only Panels runtime."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from planner.conversation.api import build_conversation_runtime
from planner.conversation.api import router as conversation_router
from planner.conversation.contracts import ConversationSystem
from planner.conversation.production_backends import production_backend_child_factories
from planner.core.clock import Clock
from planner.core.config import HOST, Config
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.core.sse import change_stream
from planner.core.testmode import build_test_router
from planner.core.trusted_ingress import TrustedIngressMiddleware, trusted_ingress_config
from planner.days.api import router as days_router
from planner.environments.vps_status import VpsStatusSnapshot, collect_vps_status
from planner.files.api import router as files_router
from planner.projects.api import router as projects_router
from planner.sprints.api import router as sprints_router
from planner.tickets.api import router as tickets_router
from planner.worker_context.service import SqliteWorkerContextService
from planner.worker_settings.api import router as worker_settings_router
from planner.worker_types.configuration import (
    configured_worker_runtime_definitions,
)

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.not_found: 404,
    ErrorCode.stale_claim: 409,
    ErrorCode.already_running: 409,
    ErrorCode.gateway_offline: 503,
}


def resolve_application_root(
    *,
    environment: Mapping[str, str] | None = None,
    module_file: Path | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    app_root = values.get("PLAN_APP_ROOT")
    if app_root is not None:
        return Path(app_root).expanduser().resolve()
    return (Path(__file__) if module_file is None else module_file).resolve().parents[3]


_REPO_ROOT = resolve_application_root()
_PREFERRED_WORKER_WORKSPACE_ROOT = Path.home() / "Coding"
_WEB_DIST = _REPO_ROOT / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"
_ASSETS_DIR = _REPO_ROOT / "assets"
_STATIC_DIR = _REPO_ROOT / "static"


def resolve_worker_workspace_root() -> Path:
    if _PREFERRED_WORKER_WORKSPACE_ROOT.is_dir():
        return _PREFERRED_WORKER_WORKSPACE_ROOT.resolve(strict=False)
    return _REPO_ROOT.resolve(strict=False)


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


async def _stop_runtime_with_deadline(runtime: Any, deadline: float) -> None:
    await runtime.stop(deadline=deadline)


def create_app(
    config: Config,
    clock: Clock,
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    conversation_system_for_test: ConversationSystem | None = None,
    vps_status_collector: Callable[[Config], VpsStatusSnapshot] | None = None,
) -> FastAPI:
    if conversation_system_for_test is not None and not config.test_mode:
        raise ValueError("conversation_system_for_test is accepted only in test mode")

    @asynccontextmanager
    async def _configured_lifespan(app: FastAPI) -> AsyncIterator[None]:
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
        from planner.tickets import data as tickets_data

        audit_conn = conn_factory()
        try:
            tickets_data.audit_ticket_registry_integrity(audit_conn)
        finally:
            audit_conn.close()

        # The conversation system, built before anything that sends into one. It composes
        # the three real agents on this machine, and spawns none of them until a
        # conversation has something to send. Everything that starts or steers a worker
        # goes through it, so it has to exist before the readiness loop starts.
        conversation = build_conversation_runtime(
            db_path=config.db_path,
            db_busy_timeout_ms=config.db_busy_timeout_ms,
            sse_heartbeat_ms=config.sse_heartbeat_ms,
            backend_child_factories=production_backend_child_factories(
                # Where this server is answering, so the `panels` CLI in an agent's shell
                # can reach it. Read from the running config rather than stored with a
                # conversation, so a conversation resumed after a restart reaches the
                # server that resumed it.
                panels_server_url=f"http://{HOST}:{config.port}",
            ),
        )
        app.state.conversation = conversation
        # A test that drives workers wants a conversation system it can hold still, so it
        # passes one in. Nothing else does: production always runs the real one.
        app.state.conversation_system = (
            conversation.system if conversation_system_for_test is None
            else conversation_system_for_test
        )
        await conversation.system.start_idle_child_janitor()

        loops: Any = None
        if not config.test_mode:
            from planner.core.loops import start_background_loops

            loops = start_background_loops(
                config,
                clock,
                conversation_system=app.state.conversation_system,
                worker_context_service=app.state.worker_context_service,
                asyncio_loop=asyncio.get_running_loop(),
            )
        try:
            yield
        finally:
            deadline = _monotonic() + float(config.shutdown_grace_seconds)
            try:
                if loops is not None:
                    await _stop_runtime_with_deadline(loops, deadline)
            finally:
                app.state.conversation = None
                await conversation.shutdown()

    app = FastAPI(title="planner", version="2.0.0", lifespan=_configured_lifespan)
    app.add_middleware(TrustedIngressMiddleware, config=trusted_ingress_config(config))
    app.state.config = config
    app.state.clock = clock
    app.state.conn_factory = conn_factory
    app.state.worker_context_service = SqliteWorkerContextService(
        lambda: connect(config.db_path, config.db_busy_timeout_ms)
    )
    app.state.conversation = None
    # The conversation system is the running one, so it belongs to the lifespan that
    # starts and stops it. Outside that window there is none.
    app.state.conversation_system = None
    configured_vps_status_collector = vps_status_collector or (
        lambda status_config: collect_vps_status(status_config, application_root=_REPO_ROOT)
    )

    @app.exception_handler(PlannerError)
    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())

    for domain_router in (
        tickets_router,
        projects_router,
        sprints_router,
        days_router,
        worker_settings_router,
    ):
        app.include_router(domain_router, prefix="/api")
    app.include_router(files_router)
    app.include_router(conversation_router, prefix="/api/conversation")

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        return {
            "test_mode": config.test_mode,
            "app_sha": config.app_sha,
        }

    @app.get("/api/health")
    async def health(expected_sha: str | None = None) -> JSONResponse:
        app_sha = config.app_sha
        if app_sha is None:
            if config.test_mode:
                return JSONResponse(status_code=200, content={"ready": True, "app_sha": None})
            return JSONResponse(
                status_code=503, content={"ready": False, "error": "missing app SHA"}
            )
        if expected_sha is None or expected_sha != app_sha:
            return JSONResponse(
                status_code=503, content={"ready": False, "error": "expected app SHA required"}
            )
        return JSONResponse(status_code=200, content={"ready": True, "app_sha": app_sha})

    @app.get("/api/vps-status")
    async def vps_status() -> dict[str, object]:
        return configured_vps_status_collector(config).as_dict()

    @app.get("/api/worker-types")
    async def worker_types() -> dict[str, Any]:
        registry = configured_worker_runtime_definitions().worker_type_registry
        return {
            "worker_types": [
                registry.manifest(worker_type) for worker_type in registry.registered_worker_types()
            ],
        }

    @app.get("/api/changes")
    async def changes() -> StreamingResponse:
        return StreamingResponse(
            change_stream(config.sse_heartbeat_ms),
            media_type="text/event-stream",
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
