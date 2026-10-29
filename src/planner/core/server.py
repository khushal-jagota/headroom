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
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from planner.conversation.api import (
    router as conversation_router,
)
from planner.conversation.backend_state import DurableCodexLoginRejection
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import ConversationBackendKey, ConversationSystem
from planner.conversation.history.api import router as conversation_history_router
from planner.conversation.production_backends import production_backend_child_factories
from planner.conversation.send_body_limit import ConversationSendBodyLimitMiddleware
from planner.conversation.usage.api import router as recorded_usage_router
from planner.conversation.usage.composition import makeUsageService
from planner.conversation.video_uploads.api import create_router as create_video_upload_router
from planner.conversation.video_uploads.config import CLEANUP_INTERVAL_SECONDS
from planner.conversation.video_uploads.destinations import authorize_destination
from planner.conversation.video_uploads.service import VideoUploadService
from planner.conversation.views.api import router as conversation_views_router
from planner.core import change_signal
from planner.core.clock import Clock
from planner.core.config import HOST, Config
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
from planner.core.web_assets import WebAssets
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.update_admission import matching_commit, startup_requires_commit
from planner.environments.update_api import router as updates_router
from planner.environments.update_provider import GitHubUpdateProvider
from planner.environments.update_requests import FileUpdateRequestStore
from planner.environments.update_service import UpdateService
from planner.environments.vps_metrics.api import router as vps_metrics_router
from planner.environments.vps_metrics.config import load_config as load_metrics_config
from planner.environments.vps_metrics.config import store_path
from planner.environments.vps_metrics.runtime import MetricsRuntime
from planner.environments.vps_status import (
    VpsStatusSnapshot,
    VpsStatusSummary,
    collect_vps_status,
    collect_vps_status_summary,
)
from planner.feedback.api import router as feedback_router
from planner.files.api import router as files_router
from planner.files.catalog.api import router as artifact_catalog_router
from planner.ideas.api import router as ideas_router
from planner.managers.api import legacy_router as legacy_managers_router
from planner.managers.api import router as managers_router
from planner.membership.api import router as membership_router
from planner.message_delivery.api import router as message_delivery_router
from planner.navigation.delivery import create_delivery_runtime
from planner.navigation.middleware import NavigationCommandFenceMiddleware
from planner.navigation.views.delivery import ScopedDeliveryRuntime
from planner.notifications.api import router as notifications_router
from planner.previews.api import handle_preview_error
from planner.previews.api import router as previews_router
from planner.previews.contracts import PreviewError
from planner.previews.runtime import PreviewRuntime
from planner.previews.service import PreviewService
from planner.previews.transport import PreviewTransport
from planner.projects.api import router as projects_router
from planner.resource_versions.api import router as resource_versions_router
from planner.scheduled_tickets.api import router as scheduled_tickets_router
from planner.server_lifecycle.runtime import application_runtime
from planner.skill_versions import (
    reconcile_managed_skill_versions,
    reconcile_provisional_worker_step_bindings,
)
from planner.tickets.api import router as tickets_router
from planner.tickets.workspace.api import router as ticket_workspace_router
from planner.worker_settings.api import router as worker_settings_router
from planner.worker_types.configuration import (
    configured_worker_runtime_definitions,
)

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.not_found: 404,
    ErrorCode.diagnostic_expired: 410,
    ErrorCode.stale_claim: 409,
    ErrorCode.already_running: 409,
    ErrorCode.gateway_offline: 503,
    ErrorCode.skill_save_incomplete: 500,
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
    identity = f'<meta name="panels-app-sha" content="{html.escape(app_sha or "", quote=True)}">'
    return document.replace("</head>", f"{identity}</head>", 1)


def http_status_for(code: ErrorCode) -> int:
    return _STATUS_BY_CODE.get(code, 400)


async def handle_planner_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(PlannerError, exc)
    return JSONResponse(status_code=http_status_for(error.code), content=error.to_payload())


async def _stop_runtime_with_deadline(runtime: Any, deadline: float) -> None:
    await runtime.stop(deadline=deadline)


def create_app(
    config: Config,
    clock: Clock,
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    conversation_system_for_test: ConversationSystem | None = None,
    backend_child_factories_for_test: Mapping[ConversationBackendKey, BackendChildFactory]
    | None = None,
    vps_status_collector: Callable[[Config], VpsStatusSnapshot] | None = None,
    vps_status_summary_collector: (
        Callable[[Config, str | None, str | None], VpsStatusSummary] | None
    ) = None,
) -> FastAPI:
    if conversation_system_for_test is not None and not config.test_mode:
        raise ValueError("conversation_system_for_test is accepted only in test mode")
    if backend_child_factories_for_test is not None and not config.test_mode:
        raise ValueError("backend_child_factories_for_test is accepted only in test mode")

    @asynccontextmanager
    async def _configured_lifespan(app: FastAPI) -> AsyncIterator[None]:
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
        from planner.tickets import data as tickets_data

        recovery_task: asyncio.Task[None] | None = None
        lifecycle_observer: asyncio.Task[None] | None = None
        video_cleanup_task: asyncio.Task[None] | None = None
        loops: Any = None
        metrics: MetricsRuntime | None = None
        release_task: asyncio.Task[None] | None = None

        async def shutdown_dependents() -> None:
            await preview_runtime.close()
            if release_task is not None and not release_task.done():
                release_task.cancel()
                try:
                    await release_task
                except asyncio.CancelledError:
                    pass
            app.state.conversation = None
            if video_cleanup_task is not None:
                video_cleanup_task.cancel()
                try:
                    await video_cleanup_task
                except asyncio.CancelledError:
                    pass
            if recovery_task is not None:
                recovery_task.cancel()
                try:
                    await recovery_task
                except asyncio.CancelledError:
                    pass
            deadline = _monotonic() + float(config.shutdown_grace_seconds)
            try:
                try:
                    try:
                        await scoped_navigation.close()
                    finally:
                        await navigation_delivery.close()
                finally:
                    try:
                        if metrics is not None:
                            await metrics.stop(deadline=deadline)
                    finally:
                        app.state.vps_metrics = None
                        if loops is not None:
                            await _stop_runtime_with_deadline(loops, deadline)
            finally:
                if lifecycle_observer is not None:
                    lifecycle_observer.cancel()
                    try:
                        await lifecycle_observer
                    except asyncio.CancelledError:
                        pass

        remembered_codex_login_rejection = DurableCodexLoginRejection(
            config.db_path, busy_timeout_ms=config.db_busy_timeout_ms
        )
        async with application_runtime(
            db_path=config.db_path,
            db_busy_timeout_ms=config.db_busy_timeout_ms,
            sse_heartbeat_ms=config.sse_heartbeat_ms,
            backend_child_factories=(
                backend_child_factories_for_test
                if backend_child_factories_for_test is not None
                else production_backend_child_factories(
                    panels_server_url=f"http://{HOST}:{config.port}",
                    remembered_codex_login_rejection=remembered_codex_login_rejection,
                )
            ),
            remembered_codex_login_rejection=remembered_codex_login_rejection,
            navigation_delivery=navigation_delivery,
            shutdown_dependents=shutdown_dependents,
            defer_view_cleanup=True,
        ) as conversation:
            audit_conn = conn_factory()
            try:
                tickets_data.audit_ticket_registry_integrity(audit_conn)
                reconcile_managed_skill_versions(audit_conn)
                reconcile_provisional_worker_step_bindings(audit_conn)
            finally:
                audit_conn.close()

            app.state.conversation_system = (
                conversation.system
                if conversation_system_for_test is None
                else conversation_system_for_test
            )

            async def release_normal_work() -> None:
                nonlocal recovery_task, video_cleanup_task, loops, metrics
                # A matching runner commit is irreversible even if later setup fails.
                app.state.update_startup_pending = False
                change_signal.emit()
                conversation.live_tail.views.cleanup_stale_epochs()
                from planner.conversation.deploy_interruptions.coordinator import (
                    run_automatic_recovery,
                )

                async def recover_conversations() -> None:
                    with conversation.system.workspace_admission.operation() as admitted:
                        if admitted:
                            await run_automatic_recovery(conversation.system, conversation.store)

                recovery_task = asyncio.create_task(recover_conversations())
                app.state.conversation = conversation
                await asyncio.to_thread(video_uploads.recover_and_cleanup)
                video_cleanup_task = asyncio.create_task(_clean_video_uploads())
                # A test that drives workers wants a conversation system it can hold still, so it
                # passes one in. Nothing else does: production always runs the real one.
                if not config.test_mode:
                    from planner.core.loops import prepare_background_loops

                    loops = prepare_background_loops(
                        config,
                        clock,
                        conversation_system=app.state.conversation_system,
                        asyncio_loop=asyncio.get_running_loop(),
                        before_readiness_poll=lambda: app.state.updates.dispatch_pending(),
                    )

                from planner.environments.vps_metrics.collectors import LinuxCollector
                from planner.environments.vps_metrics.data import Store

                metrics_config = load_metrics_config()
                metrics_path = store_path(config.db_path)
                metrics = MetricsRuntime(
                    metrics_path,
                    Store(metrics_path, metrics_config),
                    None if config.test_mode else LinuxCollector(metrics_config),
                    metrics_config,
                )
                app.state.vps_metrics = metrics
                metrics.start()
                await navigation_delivery.start()
                await scoped_navigation.start()
                await conversation.system.start_idle_child_janitor()
                if loops is not None:
                    loops.start(config.tick_seconds)
                app.state.update_startup_pending = False
                change_signal.emit()

            def lifecycle_changed() -> None:
                nonlocal release_task
                change_signal.emit()
                if (
                    app.state.update_startup_pending
                    and release_task is None
                    and matching_commit(deployment_lifecycle_store, config.app_sha)
                ):
                    release_task = asyncio.create_task(release_normal_work())

                    def report_setup_result(task: asyncio.Task[None]) -> None:
                        if not task.cancelled() and task.exception() is not None:
                            app.state.update_startup_detail = (
                                f"App {config.app_sha} is installed. "
                                "Deferred startup setup failed. Inspect the server log."
                            )
                            import logging

                            logging.getLogger(__name__).error(
                                "Deferred setup failed after app %s committed",
                                config.app_sha,
                                exc_info=task.exception(),
                            )
                            change_signal.emit()

                    release_task.add_done_callback(report_setup_result)

            if not app.state.update_startup_pending:
                await release_normal_work()
            lifecycle_observer = asyncio.create_task(
                observe_path_changes(deployment_lifecycle_path, lifecycle_changed)
            )
            # Close the read/observer-install race without a second watcher.
            lifecycle_changed()
            await preview_runtime.start()
            yield

    video_uploads = VideoUploadService(config.db_path)

    async def _clean_video_uploads() -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            system = app.state.conversation_system
            if system is not None:
                with system.workspace_admission.operation() as admitted:
                    if admitted:
                        await asyncio.to_thread(video_uploads.recover_and_cleanup)

    navigation_delivery = create_delivery_runtime(config.db_path)
    scoped_navigation = ScopedDeliveryRuntime(navigation_delivery)
    app = FastAPI(title="planner", version="2.0.0", lifespan=_configured_lifespan)
    preview_service = PreviewService(
        config.db_path, config.trusted_ingress_canonical_origin or "", panels_port=config.port
    )
    preview_transport = PreviewTransport(preview_service)
    preview_runtime = PreviewRuntime(preview_service, preview_transport)
    app.state.previews = preview_service
    app.state.preview_transport = preview_transport
    app.state.video_uploads = video_uploads
    app.add_middleware(
        NavigationCommandFenceMiddleware,
        conn_factory=conn_factory,
        db_path=config.db_path,
    )
    app.state.navigation_delivery = navigation_delivery
    app.add_middleware(ConversationSendBodyLimitMiddleware)
    app.add_middleware(TrustedIngressMiddleware, config=trusted_ingress_config(config))
    app.state.config = config
    app.state.clock = clock
    app.state.conn_factory = conn_factory
    app.state.recorded_usage = makeUsageService(config, now=clock.now)
    app.state.conversation = None
    app.state.vps_metrics = None
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
        Path(config.db_path).expanduser().resolve(strict=False).parent / "deployment-lifecycle.json"
    )
    deployment_lifecycle_store = DeploymentLifecycleStore(deployment_lifecycle_path)
    app.state.deployment_lifecycle_store = deployment_lifecycle_store
    update_request_store = FileUpdateRequestStore(
        deployment_lifecycle_path.with_name("owner-update-request.json")
    )
    recorded_update = update_request_store.read()
    applying_candidate = bool(
        recorded_update is not None
        and recorded_update.state == "applying"
        and recorded_update.release.manifest.app_sha == config.app_sha
    )
    app.state.update_startup_pending = startup_requires_commit(
        deployment_lifecycle_store, config.app_sha
    ) or (applying_candidate and not matching_commit(deployment_lifecycle_store, config.app_sha))
    app.state.update_admission_closed = False
    app.state.update_startup_detail = None
    app.state.updates = UpdateService(
        GitHubUpdateProvider.configured(),
        update_request_store,
        lambda: app.state.conversation_system,
        current_app_sha=config.app_sha,
        availability_path=deployment_lifecycle_path.with_name("owner-update-availability.json"),
        can_refresh=lambda: not app.state.update_startup_pending,
    )

    @app.middleware("http")
    async def update_admission(request: Request, call_next: Any) -> Any:
        path = request.url.path
        browser_sha = request.headers.get("X-Panels-App-SHA")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and browser_sha is not None
            and browser_sha != config.app_sha
        ):
            return JSONResponse(
                status_code=409,
                content=PlannerError(
                    ErrorCode.validation, "Refresh page before making changes.", {}
                ).to_payload(),
            )
        status_path = path in {"/api/health", "/api/meta", "/api/deployment-status", "/api/updates"}
        static_path = not path.startswith("/api/") and request.method in {"GET", "HEAD"}
        if (
            (app.state.update_startup_pending or app.state.update_admission_closed)
            and not status_path
            and not static_path
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Normal work waits for the deployment runner. "
                        "Recovery requires the owner if progress expires."
                    )
                },
            )
        system = app.state.conversation_system
        if (
            system is not None
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not path.startswith("/api/updates")
            and not status_path
        ):
            with system.workspace_admission.operation() as admitted:
                if not admitted:
                    return JSONResponse(
                        status_code=503, content={"detail": "Owner update in progress."}
                    )
                return await call_next(request)
        return await call_next(request)

    app.state.resource_version_epoch = str(uuid4())

    app.add_exception_handler(PlannerError, handle_planner_error)
    app.add_exception_handler(PreviewError, handle_preview_error)

    # Every router the application serves is included through here, so this is where the
    # live streams among them are collected. The compression below has to know them.
    event_stream_patterns: list[re.Pattern[str]] = []

    def include_router(router: APIRouter, prefix: str = "") -> None:
        app.include_router(router, prefix=prefix)
        event_stream_patterns.extend(event_stream_route_patterns(router.routes, prefix))

    for domain_router in (
        updates_router,
        tickets_router,
        ticket_workspace_router,
        resource_versions_router,
        artifact_catalog_router,
        conversation_history_router,
        projects_router,
        managers_router,
        legacy_managers_router,
        ideas_router,
        scheduled_tickets_router,
        notifications_router,
        feedback_router,
        worker_settings_router,
        message_delivery_router,
        membership_router,
        vps_metrics_router,
    ):
        include_router(domain_router, prefix="/api")
    include_router(previews_router)
    include_router(navigation_delivery.router)
    include_router(scoped_navigation.router)
    include_router(files_router)
    include_router(conversation_router, prefix="/api/conversation")
    include_router(
        create_video_upload_router(
            video_uploads,
            lambda destination: authorize_destination(
                destination, conn_factory=conn_factory, runtime=app.state.conversation
            ),
        )
    )
    include_router(conversation_views_router, prefix="/api/conversation")
    include_router(recorded_usage_router, prefix="/api/conversation")

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
        projection = await asyncio.to_thread(deployment_lifecycle_store.project, config.app_sha)
        payload = projection.to_public_dict()
        if app.state.update_startup_pending and projection.state == "unknown":
            payload["detail"] = (
                "Recovery required: candidate admission lacks a readable runner commit. "
                "The owner must inspect the deployment runner and recorded recovery paths."
            )
        if app.state.update_startup_detail is not None:
            payload["detail"] = app.state.update_startup_detail
        return payload

    @app.get("/api/vps-status-summary")
    async def vps_status_summary() -> dict[str, object]:
        projection = await asyncio.to_thread(deployment_lifecycle_store.project, config.app_sha)
        summary = await asyncio.to_thread(
            configured_vps_status_summary_collector,
            config,
            projection.outcome,
            projection.detail,
        )
        return summary.as_dict()

    @app.get("/api/worker-types")
    async def worker_types(supported: bool = False) -> dict[str, Any]:
        registry = configured_worker_runtime_definitions().worker_type_registry
        worker_type_ids = (
            registry.supported_worker_types() if supported else registry.registered_worker_types()
        )
        return {
            "worker_types": [registry.manifest(worker_type) for worker_type in worker_type_ids],
        }

    @app.get("/api/worker-types/{worker_type}/manifest")
    async def worker_type_manifest(worker_type: str) -> dict[str, Any]:
        """One Worker type, in the same shape the whole list arrives in.

        A Ticket page draws the stages of one type and was reading all fourteen, 20,165
        bytes, to find it. Answering in the list's shape keeps every reader of that shape
        — the lifecycle, the stage table, the section admission — untouched.
        """
        registry = configured_worker_runtime_definitions().worker_type_registry
        return {"worker_types": [registry.manifest(worker_type)]}

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
        app.mount("/_app", WebAssets(directory=_WEB_DIST, html=True), name="vite_app")
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
