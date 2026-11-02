"""Update admission without a response bridge that mistakes stream disconnects for errors."""

from __future__ import annotations

from contextlib import ExitStack

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from planner.conversation.contracts import ConversationSystem
from planner.core.config import Config
from planner.core.errors import ErrorCode, PlannerError


class UpdateAdmissionMiddleware:
    def __init__(self, app: ASGIApp, *, config: Config) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path
        browser_sha = request.headers.get("X-Panels-App-SHA")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and browser_sha is not None
            and browser_sha != self.config.app_sha
        ):
            await JSONResponse(
                status_code=409,
                content=PlannerError(
                    ErrorCode.validation, "Refresh page before making changes.", {}
                ).to_payload(),
            )(scope, receive, send)
            return

        state = request.app.state
        status_path = path in {"/api/health", "/api/meta", "/api/deployment-status", "/api/updates"}
        static_path = not path.startswith("/api/") and request.method in {"GET", "HEAD"}
        if (
            (state.update_startup_pending or state.update_admission_closed)
            and not status_path
            and not static_path
        ):
            await JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Installation is unconfirmed. Work remains paused. "
                        "Chief review is required."
                    )
                },
            )(scope, receive, send)
            return

        system: ConversationSystem | None = state.conversation_system
        if (
            system is not None
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not path.startswith("/api/updates")
            and not status_path
        ):
            with ExitStack() as lease:
                if not lease.enter_context(system.workspace_admission.operation()):
                    await JSONResponse(
                        status_code=503, content={"detail": "Owner update in progress."}
                    )(scope, receive, send)
                    return

                async def release_at_response_start(message: Message) -> None:
                    if message["type"] == "http.response.start":
                        # call_next previously released the lease when headers arrived,
                        # before their upstream send. A body must not extend that lease.
                        lease.close()
                    await send(message)

                # ExitStack releases once, also on failure or cancellation before headers.
                await self.app(scope, receive, release_at_response_start)
            return

        await self.app(scope, receive, send)
