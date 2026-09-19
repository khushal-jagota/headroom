"""FastAPI application composition for the ACP-only Panels runtime."""

from __future__ import annotations

import asyncio
import html
import os
import re
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from planner.conversation.api import build_conversation_runtime
from planner.conversation.api import router as conversation_router
from planner.conversation.contracts import ConversationSystem
from planner.conversation.production_backends import production_backend_child_factories
from planner.conversation.send_body_limit import ConversationSendBodyLimitMiddleware
from planner.core import change_signal
from planner.core.clock import Clock
from planner.core.config import HOST, Config
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.core.path_observer import observe_path_changes
from planner.core.response_compression import (
    CompressExceptEventStreams,
    answers_with_an_event_stream,
    event_stream_route_patterns,
)
from planner.core.sse import change_stream
from planner.core.testmode import build_test_router
from planner.core.trusted_ingress import TrustedIngressMiddleware, trusted_ingress_config
from planner.days.api import router as days_router
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.vps_status import (
    VpsStatusSnapshot,
    VpsStatusSummary,
    collect_vps_status,
    collect_vps_status_summary,
)
from planner.feedback.api import router as feedback_router
from planner.files.api import router as files_router
from planner.judgments.api import router as judgments_router
from planner.membership.api import router as membership_router
from planner.message_delivery.api import router as message_delivery_router
from planner.notifications.api import router as notifications_router
from planner.projects.api import router as projects_router
from planner.scheduled_tickets.api import router as scheduled_tickets_router
from planner.skill_versions import (
    reconcile_managed_skill_versions,
    reconcile_provisional_worker_step_bindings,
)
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
_PREFERRED_WORKER_WORKSPACE_ROOT = Path.home() / "projects"
_WEB_DIST = _REPO_ROOT / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"
_ASSETS_DIR = _REPO_ROOT / "assets"
_STATIC_DIR = _REPO_ROOT / "static"


def resolve_worker_workspace_root() -> Path:
    if _PREFERRED_WORKER_WORKSPACE_ROOT.is_dir():
        return _PREFERRED_WORKER_WORKSPACE_ROOT.resolve(strict=False)
    return _REPO_ROOT.resolve(strict=False)


def svelte_index_html(app_sha: str | None = None) -> str:
    if _WEB_INDEX.is_file():
        document = _WEB_INDEX.read_text(encoding="utf-8")
    else:
        document = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            "<title>Panels</title></head><body>"
            '<div id="app" data-svelte-app>'
            "web/dist is missing; run npm --prefix web run build."
            "</div></body></html>"
        )
    identity = (
        '<meta name="panels-app-sha" '
        f'content="{html.escape(app_sha or "", quote=True)}">'
    )
    return document.replace("</head>", f"{identity}</head>", 1)


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
    vps_status_summary_collector: (
        Callable[[Config, str | None, str | None], VpsStatusSummary] | None
    ) = None,
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
            reconcile_managed_skill_versions(audit_conn, Path(config.db_path).expanduser().parent)
            reconcile_provisional_worker_step_bindings(audit_conn)
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

        lifecycle_observer = asyncio.create_task(
            observe_path_changes(deployment_lifecycle_path, change_signal.emit)
        )
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
                lifecycle_observer.cancel()
                try:
                    await lifecycle_observer
                except asyncio.CancelledError:
                    pass
                finally:
                    app.state.conversation = None
                    await conversation.shutdown()

    app = FastAPI(title="planner", version="2.0.0", lifespan=_configured_lifespan)
    app.add_middleware(ConversationSendBodyLimitMiddleware)
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
    configured_vps_status_summary_collector = vps_status_summary_collector or (
        lambda status_config, outcome, detail: collect_vps_status_summary(
            status_config,
            deployment_outcome=outcome,
            deployment_detail=detail,
        )
    )
    deployment_lifecycle_path = (
        Path(config.db_path).expanduser().resolve(strict=False).parent
        / "deployment-lifecycle.json"
    )
    deployment_lifecycle_store = DeploymentLifecycleStore(deployment_lifecycle_path)

    @app.exception_handler(PlannerError)
    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())

    # Every router the application serves is included through here, so this is where the
    # live streams among them are collected. The compression below has to know them.
    event_stream_patterns: list[re.Pattern[str]] = []

    def include_router(router: APIRouter, prefix: str = "") -> None:
        app.include_router(router, prefix=prefix)
        event_stream_patterns.extend(event_stream_route_patterns(router.routes, prefix))

    for domain_router in (
        tickets_router,
        judgments_router,
        projects_router,
        sprints_router,
        days_router,
        scheduled_tickets_router,
        notifications_router,
        feedback_router,
        worker_settings_router,
        message_delivery_router,
        membership_router,
    ):
        include_router(domain_router, prefix="/api")
    include_router(files_router)
    include_router(conversation_router, prefix="/api/conversation")
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
        snapshot = await asyncio.to_thread(configured_vps_status_collector, config)
        return snapshot.as_dict()

    @app.get("/api/deployment-status")
    async def deployment_status() -> dict[str, object]:
        projection = await asyncio.to_thread(
            deployment_lifecycle_store.project, config.app_sha
        )
        return projection.to_public_dict()

    @app.get("/api/vps-status-summary")
    async def vps_status_summary() -> dict[str, object]:
        projection = await asyncio.to_thread(
            deployment_lifecycle_store.project, config.app_sha
        )
        summary = await asyncio.to_thread(
            configured_vps_status_summary_collector,
            config,
            projection.outcome,
            projection.detail,
        )
        return summary.as_dict()

    @app.get("/api/worker-types")
    async def worker_types() -> dict[str, Any]:
        registry = configured_worker_runtime_definitions().worker_type_registry
        return {
            "worker_types": [
                registry.manifest(worker_type) for worker_type in registry.registered_worker_types()
            ],
        }

    @app.get("/api/changes")
    @answers_with_an_event_stream
    async def changes() -> StreamingResponse:
        return StreamingResponse(
            change_stream(config.sse_heartbeat_ms),
            media_type="text/event-stream",
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(
            content=svelte_index_html(config.app_sha),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/service-worker.js", response_class=FileResponse)
    async def service_worker() -> FileResponse:
        return FileResponse(
            _STATIC_DIR / "service-worker.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    if config.test_mode:
        include_router(build_test_router(config, clock), prefix="/api")
    if _WEB_DIST.is_dir():
        app.mount("/_app", StaticFiles(directory=_WEB_DIST, html=True), name="vite_app")
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    # Reads are large and the link is remote, so bodies travel compressed. Level 4 is
    # the measured knee on the largest real body: it costs 100 ms of CPU and saves
    # 7.5 MB, where level 9 spends 245 ms for 0.12 MB more. The change signal and the
    # conversation tail go around the compression entirely, because the middleware holds
    # every response's headers back until a body arrives, and a live stream that has
    # nothing to say yet has no body to release them with.
    app.add_middleware(
        CompressExceptEventStreams,
        event_stream_patterns=(
            *event_stream_patterns,
            *event_stream_route_patterns(app.routes),
        ),
        minimum_size=1024,
        compresslevel=4,
    )
    return app
