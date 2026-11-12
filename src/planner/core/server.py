"""FastAPI application composition for the ACP-only Panels runtime."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from planner.attention.api import router as attention_router
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
from planner.conversation.views.rebuild import rebuild_out_of_date_views
from planner.core import change_signal
from planner.core.clock import Clock
from planner.core.config import (
    DB_BUSY_TIMEOUT_MS,
    HOST,
    SHUTDOWN_GRACE_SECONDS,
    SSE_HEARTBEAT_MS,
    Config,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.core.path_observer import observe_path_changes
from planner.core.request_guidance import preserve_authored_request_examples
from planner.core.response_compression import (
    CompressExceptMarkedRoutes,
    answers_uncompressed,
    uncompressed_route_patterns,
)
from planner.core.sse import change_stream
from planner.core.testmode import build_test_router
from planner.core.trusted_ingress import TrustedIngressMiddleware, trusted_ingress_config
from planner.core.web_assets import WebAssets
from planner.credentials.api import router as credentials_router
from planner.credentials.requests.api import router as credential_requests_router
from planner.datasets.api import router as datasets_router
from planner.environments import ticket_scratch
from planner.environments.hermes_home import provision_planner_home_skills, resolve_planner_home
from planner.environments.logic.update_experience import deployment_status
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_admission_middleware import UpdateAdmissionMiddleware
from planner.environments.update_api import router as updates_router
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    attempt_path,
    import_legacy_request,
)
from planner.environments.update_configuration import DEPLOYMENT_SUCCESS_DISPLAY_SECONDS
from planner.environments.update_install import own_service_unit
from planner.environments.update_provider import GitHubUpdateProvider
from planner.environments.update_service import UpdateService
from planner.environments.vps_metrics.api import router as vps_metrics_router
from planner.environments.vps_metrics.config import load_config as load_metrics_config
from planner.environments.vps_metrics.config import store_path
from planner.environments.vps_metrics.runtime import MetricsRuntime
from planner.environments.vps_status import VpsStatusSnapshot, collect_vps_status
from planner.executor.clock import now_ms as executor_now_ms
from planner.files.api import router as files_router
from planner.files.catalog.api import router as artifact_catalog_router
from planner.goals.api import router as goals_router
from planner.ideas.api import router as ideas_router
from planner.ios_compatibility.middleware import IOSBuildGateMiddleware
from planner.managers.api import router as managers_router
from planner.measure_sources.api import router as measure_sources_router
from planner.measure_sources.participants import SlotParticipantFactory
from planner.measures.api import router as measures_router
from planner.membership.api import router as membership_router
from planner.message_delivery.api import router as message_delivery_router
from planner.navigation.delivery import create_delivery_runtime
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
from planner.runtime.workspaces import register_workspace_root
from planner.server_lifecycle.runtime import application_runtime
from planner.skill_sources import provision_native_backend_skills, provision_workspace_skills
from planner.stalls import checker as stalls_checker
from planner.stalls.contracts import StallChecker, StallCheckerFactory
from planner.stalls.registry import WAITS
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
    ErrorCode.executor_version_refused: 409,
    ErrorCode.executor_incarnation_not_current: 409,
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


def _with_native_build_1_fields(health: dict[str, object]) -> dict[str, object]:
    """Native build 1 (ios/Panels/Models/WorkspaceModels.swift, ExecutorHealth) requires
    connection_state, and knows the Mac's activity only when it is "ready" and the
    identity's connection_generation is above 0. Protocol 22 has neither, so both are
    derived from presence here. Remove when the minimum native build reads presence."""
    present = health.get("presence") == "present"
    identity = health.get("identity")
    if isinstance(identity, dict):
        generation = identity.get("connection_generation", 0)
        identity = {**identity, "connection_generation": max(int(generation), 1 if present else 0)}
    return {
        **health,
        "identity": identity,
        "connection_state": "ready" if present else "disconnected",
    }


def create_app(
    config: Config,
    clock: Clock,
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    conversation_system_for_test: ConversationSystem | None = None,
    slot_participants_for_test: SlotParticipantFactory | None = None,
    backend_child_factories_for_test: Mapping[ConversationBackendKey, BackendChildFactory]
    | None = None,
    vps_status_collector: Callable[[Config], VpsStatusSnapshot] | None = None,
    stall_checker_factory_for_test: StallCheckerFactory | None = None,
) -> FastAPI:
    if stall_checker_factory_for_test is not None and not config.test_mode:
        raise ValueError("stall_checker_factory_for_test is accepted only in test mode")
    if slot_participants_for_test is not None and not config.test_mode:
        raise ValueError("slot_participants_for_test is accepted only in test mode")
    if conversation_system_for_test is not None and not config.test_mode:
        raise ValueError("conversation_system_for_test is accepted only in test mode")
    if backend_child_factories_for_test is not None and not config.test_mode:
        raise ValueError("backend_child_factories_for_test is accepted only in test mode")

    @asynccontextmanager
    async def _configured_lifespan(app: FastAPI) -> AsyncIterator[None]:
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        from planner.tickets import data as tickets_data

        recovery_task: asyncio.Task[None] | None = None
        attempt_observer: asyncio.Task[None] | None = None
        video_cleanup_task: asyncio.Task[None] | None = None
        ticket_scratch_task: asyncio.Task[None] | None = None
        view_rebuild_task: asyncio.Task[None] | None = None
        loops: Any = None
        metrics: MetricsRuntime | None = None
        executor_endpoint = None
        stall_checker: StallChecker | None = None

        async def shutdown_dependents() -> None:
            app.state.conversation = None
            deadline = _monotonic() + float(SHUTDOWN_GRACE_SECONDS)
            failures: list[BaseException] = []

            async def attempt(operation: Awaitable[None]) -> None:
                try:
                    await operation
                except BaseException as error:
                    failures.append(error)

            async def cancel_task(task: asyncio.Task[None]) -> None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await attempt(preview_runtime.close())
            if video_cleanup_task is not None:
                await attempt(cancel_task(video_cleanup_task))
            if ticket_scratch_task is not None:
                await attempt(cancel_task(ticket_scratch_task))
            if view_rebuild_task is not None:
                await attempt(cancel_task(view_rebuild_task))
            if recovery_task is not None:
                await attempt(cancel_task(recovery_task))
            await attempt(scoped_navigation.close())
            await attempt(navigation_delivery.close())
            if metrics is not None:
                await attempt(metrics.stop(deadline=deadline))
            app.state.vps_metrics = None
            if loops is not None:
                await attempt(_stop_runtime_with_deadline(loops, deadline))
            if stall_checker is not None:
                await attempt(stall_checker.close())
            if executor_endpoint is not None:
                await attempt(executor_endpoint.close())
            if attempt_observer is not None:
                await attempt(cancel_task(attempt_observer))
            if failures:
                for later in failures[1:]:
                    failures[0].add_note(f"dependent shutdown also failed: {later!r}")
                raise failures[0]

        remembered_codex_login_rejection = DurableCodexLoginRejection(
            config.db_path, busy_timeout_ms=DB_BUSY_TIMEOUT_MS
        )
        backend_factories = (
            backend_child_factories_for_test
            if backend_child_factories_for_test is not None
            else production_backend_child_factories(
                panels_server_url=f"http://{HOST}:{config.port}",
                remembered_codex_login_rejection=remembered_codex_login_rejection,
            )
        )
        if config.executor_id is not None:
            from planner.executor.routing import LocaleBackendChildFactory
            from planner.executor.vps_transport import VpsExecutor

            executor_endpoint = VpsExecutor(
                config.db_path,
                clock,
                machine_id=config.executor_id,
                application_revision=config.app_sha or "unknown",
            )
            backend_factories = {
                key: LocaleBackendChildFactory(config.db_path, local, executor_endpoint.runs)
                for key, local in backend_factories.items()
            }
        app.state.executor_endpoint = executor_endpoint
        with register_workspace_root(config.db_path, config.workspace_root):
            from planner.tickets.integrity import TicketIntegrityError

            ticket_integrity_faults: list[TicketIntegrityError] = []

            def audit_startup_tickets(connection: sqlite3.Connection) -> None:
                ticket_integrity_faults.extend(
                    tickets_data.audit_ticket_registry_integrity(connection, startup=True)
                )

            async with application_runtime(
                db_path=config.db_path,
                db_busy_timeout_ms=DB_BUSY_TIMEOUT_MS,
                sse_heartbeat_ms=SSE_HEARTBEAT_MS,
                backend_child_factories=backend_factories,
                remembered_codex_login_rejection=remembered_codex_login_rejection,
                navigation_delivery=navigation_delivery,
                shutdown_dependents=shutdown_dependents,
                defer_view_cleanup=True,
                startup_audit=audit_startup_tickets,
            ) as conversation:
                # Mac runs survive a VPS restart: their rows, assignments and commands
                # are all in the database, and the conversation core adopts them.
                if executor_endpoint is not None:
                    executor_endpoint.configure(
                        conversation.system, app, update_check=app.state.mac_updates.check
                    )
                    remote_presence = executor_endpoint.presence
                    conversation.system.start_admission.configure_destinations(
                        lambda destination: destination == "home" or remote_presence.is_present()
                    )
                    executor_endpoint.mailbox.start_admission = conversation.system.start_admission
                    await executor_endpoint.start()
                conversation.system.configure_machine_catalog(
                    None if executor_endpoint is None else executor_endpoint.catalog
                )
                app.state.conversation_system = (
                    conversation.system
                    if conversation_system_for_test is None
                    else conversation_system_for_test
                )

                async def release_normal_work() -> None:
                    nonlocal recovery_task, video_cleanup_task, ticket_scratch_task
                    nonlocal view_rebuild_task
                    nonlocal loops, metrics, stall_checker
                    conversation.live_tail.views.cleanup_stale_epochs()
                    database_parent = Path(config.db_path).resolve().parent
                    if config.workspace_root is not None:
                        provision_workspace_skills(config.workspace_root, database_parent)
                    if installed_app:
                        # An installed release links its skills into the agent homes itself,
                        # so an update needs no separate provisioning step.
                        provision_planner_home_skills(
                            resolve_planner_home(), configured_database_parent=database_parent
                        )
                        provision_native_backend_skills(Path.home() / ".codex", database_parent)
                        provision_native_backend_skills(Path.home() / ".claude", database_parent)
                    from planner.conversation.deploy_interruptions.coordinator import (
                        run_automatic_recovery,
                    )

                    async def recover_conversations() -> None:
                        with conversation.system.workspace_admission.operation() as admitted:
                            if admitted:
                                if ticket_integrity_faults:
                                    from planner.tickets.integrity import notify_chief

                                    notice_conn = conn_factory()
                                    try:
                                        await notify_chief(
                                            notice_conn,
                                            app.state.conversation_system,
                                            clock,
                                            ticket_integrity_faults,
                                        )
                                    finally:
                                        notice_conn.close()
                                system = conversation.system
                                await system.tell_agents_of_background_tasks_lost_at_restart()
                                await run_automatic_recovery(
                                    conversation.system, conversation.store
                                )

                    recovery_task = asyncio.create_task(recover_conversations())
                    view_rebuild_task = asyncio.create_task(
                        rebuild_out_of_date_views(
                            config.db_path,
                            DB_BUSY_TIMEOUT_MS,
                            conversation.system.workspace_admission,
                        )
                    )
                    app.state.conversation = conversation
                    await asyncio.to_thread(video_uploads.recover_and_cleanup)
                    video_cleanup_task = asyncio.create_task(_clean_video_uploads())
                    scratch_layout = ticket_scratch.layout_for(
                        config.ticket_scratch_root, config.ticket_scratch_database, config.db_path
                    )
                    if not config.test_mode:
                        ticket_scratch_task = asyncio.create_task(
                            _clean_ticket_scratch(scratch_layout)
                        )
                    # Worker tests can supply a held conversation system.
                    # Production always uses the real system.
                    if not config.test_mode:
                        from planner.core.loops import prepare_background_loops

                        loops = prepare_background_loops(
                            config,
                            clock,
                            conversation_system=app.state.conversation_system,
                            asyncio_loop=asyncio.get_running_loop(),
                            before_readiness_poll=app.state.updates.poll,
                        )

                    elif slot_participants_for_test is not None:
                        from planner.core.loops import prepare_scheduled_loops

                        loops = prepare_scheduled_loops(
                            config,
                            clock,
                            workspace_admission=conversation.system.workspace_admission,
                            participants_factory=slot_participants_for_test,
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
                    stall_checker_factory: StallCheckerFactory | None = (
                        stall_checker_factory_for_test
                        if config.test_mode
                        else stalls_checker.StallChecker
                    )
                    if stall_checker_factory is not None:
                        endpoint = executor_endpoint
                        stall_checker = stall_checker_factory(
                            config.db_path,
                            WAITS,
                            (lambda: ()) if endpoint is None else endpoint.stall_sources,
                            executor_now_ms,
                        )
                        stall_checker.start()
                    await navigation_delivery.start()
                    await scoped_navigation.start()
                    await conversation.system.start_idle_child_janitor()
                    if loops is not None:
                        loops.start(config.tick_seconds)
                    app.state.updates.start(asyncio.get_running_loop())
                    change_signal.emit()

                await release_normal_work()
                # The installer writes the attempt record from outside this process.
                attempt_observer = asyncio.create_task(
                    observe_path_changes(update_attempts.path, change_signal.emit)
                )
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

    async def _clean_ticket_scratch(layout: ticket_scratch.ScratchLayout | None) -> None:
        from planner.ticket_workspaces.cleanup import clean_for_database

        logger = logging.getLogger(__name__)
        while True:
            await asyncio.sleep(ticket_scratch.CLEANUP_INTERVAL_SECONDS)
            try:
                result = await asyncio.to_thread(
                    clean_for_database,
                    config.db_path,
                    clock.now_unix(),
                    extra_roots=layout.folders if layout is not None else (),
                )
            except Exception:
                logger.exception("Ticket scratch cleanup failed")
                continue
            for kept in result.skipped:
                logger.warning("Ticket scratch kept: %s (%s)", kept["path"], kept["reason"])

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
    app.state.navigation_delivery = navigation_delivery
    app.add_middleware(ConversationSendBodyLimitMiddleware)
    app.add_middleware(TrustedIngressMiddleware, config=trusted_ingress_config(config))
    from planner.executor.app_transport import build_executor_router

    # The compression at the end of this function skips the marked routes. An included
    # router's routes do not appear in app.routes, so each router adds its own marks here.
    # The Mac update channel is among them.
    uncompressed_patterns: list[re.Pattern[str]] = []
    executor_router = build_executor_router()
    app.include_router(executor_router)
    uncompressed_patterns.extend(uncompressed_route_patterns(executor_router.routes))
    app.state.executor_id = config.executor_id
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
    data_directory = Path(config.db_path).expanduser().resolve(strict=False).parent
    current_root = data_directory.parent
    # Only an installed release under <current_root>/app installs its own updates.
    installed_app = bool(config.app_sha) and _REPO_ROOT == current_root / "app"
    # The installer restarts the unit this server runs in, and nothing else.
    service_unit = own_service_unit() if installed_app else None
    update_attempts = FileUpdateAttemptStore(attempt_path(data_directory))
    import_legacy_request(update_attempts, data_directory, config.app_sha)
    update_provider = GitHubUpdateProvider.configured() if service_unit is not None else None
    app.state.mac_updates = MacUpdateService(
        data_directory / "mac-updates",
        update_attempts,
        update_provider,
        config.app_sha,
        ready_release=lambda: app.state.updates.ready_release(),
    )
    app.state.updates = UpdateService(
        update_provider,
        update_attempts,
        lambda: app.state.conversation_system,
        connection_factory=conn_factory,
        current_root=current_root,
        database=Path(config.db_path).expanduser().resolve(strict=False),
        backup_directory=Path(config.backup_dir).expanduser(),
        current_app_sha=config.app_sha,
        health_url=f"http://{HOST}:{config.port}/api/health",
        service_unit=service_unit or "",
        prepare_mac_update=app.state.mac_updates.prepare,
        mac_phase=app.state.mac_updates.phase,
        mac_reported_at=app.state.mac_updates.reported_at,
        mac_configured=lambda: getattr(app.state, "executor_endpoint", None) is not None,
    )

    def current_deployment_status() -> dict[str, object]:
        return deployment_status(
            update_attempts.read(),
            config.app_sha,
            success_seconds=DEPLOYMENT_SUCCESS_DISPLAY_SECONDS,
            now=datetime.now(UTC),
        )

    app.state.deployment_status = current_deployment_status

    app.add_middleware(IOSBuildGateMiddleware)
    app.add_middleware(UpdateAdmissionMiddleware, config=config)

    app.state.resource_version_epoch = str(uuid4())

    app.add_exception_handler(PlannerError, handle_planner_error)
    app.add_exception_handler(PreviewError, handle_preview_error)

    # Every domain router is included through here, so its marked routes join the list.
    def include_router(router: APIRouter, prefix: str = "") -> None:
        from planner.core.authority.intake import request_admission

        app.include_router(router, prefix=prefix, dependencies=[Depends(request_admission)])
        uncompressed_patterns.extend(uncompressed_route_patterns(router.routes, prefix))

    for domain_router in (
        updates_router,
        tickets_router,
        ticket_workspace_router,
        resource_versions_router,
        artifact_catalog_router,
        conversation_history_router,
        projects_router,
        managers_router,
        ideas_router,
        measure_sources_router,
        measures_router,
        datasets_router,
        attention_router,
        goals_router,
        credentials_router,
        credential_requests_router,
        notifications_router,
        worker_settings_router,
        message_delivery_router,
        membership_router,
        vps_metrics_router,
    ):
        include_router(domain_router, prefix="/api")
    include_router(previews_router)
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

    @app.get("/api/executor/health")
    async def executor_health() -> dict[str, object]:
        endpoint = getattr(app.state, "executor_endpoint", None)
        if endpoint is None:
            return {"mode": "local", "connection_state": "disconnected"}
        return _with_native_build_1_fields({"mode": "remote", **endpoint.health_payload()})

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
    async def deployment_status_route() -> dict[str, object]:
        return await asyncio.to_thread(current_deployment_status)

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
    @answers_uncompressed
    async def changes() -> StreamingResponse:
        return StreamingResponse(
            change_stream(SSE_HEARTBEAT_MS),
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
    # 7.5 MB, where level 9 spends 245 ms for 0.12 MB more. The live streams and the Mac
    # update channel go around the compression entirely: the middleware holds a stream's
    # headers back until a body arrives, and the Mac saves update bytes exactly as sent.
    app.add_middleware(
        CompressExceptMarkedRoutes,
        uncompressed_patterns=(
            *uncompressed_patterns,
            *uncompressed_route_patterns(app.routes),
        ),
        minimum_size=1024,
        compresslevel=4,
    )
    preserve_authored_request_examples(app)
    return app
