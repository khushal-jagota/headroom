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

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from planner.conversation.composition import ConversationComposition, ConversationTestOptions
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.errors import ErrorCode, PlannerError
from planner.core.sse import change_stream
from planner.core.testmode import TestModeAcceptingEmployeeRevisionRunner, build_test_router
from planner.core.trusted_ingress import TrustedIngressMiddleware, trusted_ingress_config
from planner.days.api import router as days_router
from planner.environments.vps_status import VpsStatusSnapshot, collect_vps_status
from planner.files.api import router as files_router
from planner.projects.api import router as projects_router
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.sprints.api import router as sprints_router
from planner.tickets.api import router as tickets_router
from planner.worker_settings.api import router as worker_settings_router
from planner.worker_types.configuration import (
    configured_employee_runtime_definitions,
    install_employee_runtime_definitions_for_test,
    restore_employee_runtime_definitions_for_test,
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
    release_root = values.get("PLAN_RELEASE_ROOT")
    if release_root is not None:
        return Path(release_root).expanduser().resolve()
    return (Path(__file__) if module_file is None else module_file).resolve().parents[3]


_REPO_ROOT = resolve_application_root()
_PREFERRED_EMPLOYEE_WORKSPACE_ROOT = Path.home() / "Coding"
_WEB_DIST = _REPO_ROOT / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"
_ASSETS_DIR = _REPO_ROOT / "assets"
_STATIC_DIR = _REPO_ROOT / "static"


def resolve_employee_workspace_root() -> Path:
    if _PREFERRED_EMPLOYEE_WORKSPACE_ROOT.is_dir():
        return _PREFERRED_EMPLOYEE_WORKSPACE_ROOT.resolve(strict=False)
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
    conversation_test_options: ConversationTestOptions | None = None,
    vps_status_collector: Callable[[Config], VpsStatusSnapshot] | None = None,
) -> FastAPI:
    if conversation_test_options is not None and not config.test_mode:
        raise ValueError("conversation_test_options are accepted only in test mode")

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

        loops: Any = None
        test_runner: EmployeeStepRunner | None = None
        conversation: ConversationComposition | None = None
        if conversation_test_options is not None:
            conversation = ConversationComposition.build(
                db_path=config.db_path,
                busy_timeout_ms=config.db_busy_timeout_ms,
                clock=clock,
                repository_root=_REPO_ROOT,
                employee_workspace_root=resolve_employee_workspace_root(),
                loop=asyncio.get_running_loop(),
                test_options=conversation_test_options,
            )
            app.state.conversation = conversation
            test_runner = EmployeeStepRunner(
                config.db_path,
                clock,
                gateway=conversation.step_gateway,
                boundary_hour=config.boundary_hour,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            app.state.employee_step_runner = test_runner
        elif not config.test_mode:
            from planner.core.loops import start_background_loops

            conversation = ConversationComposition.build(
                db_path=config.db_path,
                busy_timeout_ms=config.db_busy_timeout_ms,
                clock=clock,
                repository_root=_REPO_ROOT,
                employee_workspace_root=resolve_employee_workspace_root(),
                loop=asyncio.get_running_loop(),
            )
            try:
                await conversation.run_employee_backend_startup_preflights()
                app.state.conversation = conversation
                loops = start_background_loops(
                    config,
                    clock,
                    step_gateway=conversation.step_gateway,
                )
            except BaseException:
                deadline = _monotonic() + float(config.shutdown_grace_seconds)
                await conversation.close_admission()
                await conversation.shutdown(deadline)
                app.state.conversation = None
                raise
            app.state.employee_step_runner = loops.employee_step_runner
        try:
            yield
        finally:
            deadline = _monotonic() + float(config.shutdown_grace_seconds)
            if conversation is not None:
                await conversation.close_admission()
            try:
                if loops is not None:
                    await _stop_runtime_with_deadline(loops, deadline)
                if test_runner is not None:
                    await asyncio.to_thread(test_runner.stop, deadline=deadline)
            finally:
                if conversation is not None:
                    await conversation.shutdown(deadline)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        previous_definitions = None
        if conversation_test_options is not None:
            previous_definitions = install_employee_runtime_definitions_for_test(
                conversation_test_options.employee_runtime_definitions
            )
        try:
            async with _configured_lifespan(app):
                yield
        finally:
            if previous_definitions is not None:
                restore_employee_runtime_definitions_for_test(previous_definitions)

    app = FastAPI(title="planner", version="2.0.0", lifespan=lifespan)
    app.add_middleware(TrustedIngressMiddleware, config=trusted_ingress_config(config))
    app.state.config = config
    app.state.clock = clock
    app.state.conn_factory = conn_factory
    app.state.employee_step_runner = (
        TestModeAcceptingEmployeeRevisionRunner() if config.test_mode else None
    )
    app.state.conversation = None
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

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        return {
            "test_mode": config.test_mode,
            "release_sha": config.release_sha,
        }

    @app.get("/api/health")
    async def health(expected_sha: str | None = None) -> JSONResponse:
        release_sha = config.release_sha
        if release_sha is None:
            if config.test_mode:
                return JSONResponse(status_code=200, content={"ready": True, "release_sha": None})
            return JSONResponse(
                status_code=503, content={"ready": False, "error": "missing release SHA"}
            )
        if expected_sha is None or expected_sha != release_sha:
            return JSONResponse(
                status_code=503, content={"ready": False, "error": "expected release SHA required"}
            )
        return JSONResponse(status_code=200, content={"ready": True, "release_sha": release_sha})

    @app.get("/api/vps-status")
    async def vps_status() -> dict[str, object]:
        return configured_vps_status_collector(config).as_dict()

    @app.get("/api/worker-types")
    async def worker_types() -> dict[str, Any]:
        runtime_definitions = configured_employee_runtime_definitions()
        registry = runtime_definitions.worker_type_registry
        return {
            "employee_backends": list(
                runtime_definitions.employee_backend_catalog.registered_backend_keys()
            ),
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

    @app.websocket("/api/conversation")
    async def conversation_ws(websocket: WebSocket) -> None:
        conversation = app.state.conversation
        if conversation is None:
            await websocket.close(code=1013, reason="conversation service is unavailable")
            return
        await conversation.hub.websocket(websocket)

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
