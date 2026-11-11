"""Comparison-only Python host: scripted providers and explicitly driven loop passes.

This host never starts polling threads. A source pass admits due occurrences, as the
production schedule loop does; it does not start the separate source dispatcher.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI
from tests.e2e.conversation_signals_server import NoInstalledBackends, ScriptedChild
from tests.e2e.ticket_model_controls import TicketModelSnapshots

from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import ConversationBackendKey
from planner.core.clock import build_clock
from planner.core.config import Config, load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.manager_wakes.runtime import ManagerWakeLoop
from planner.measure_sources.registry import build_participants
from planner.measure_sources.schedule_loop import SourceScheduleLoop
from planner.notifications.contracts import (
    APNsResult,
    NotificationIntent,
    PendingAPNsDelivery,
    PushSubscription,
    WebPushIdentity,
    WebPushResult,
)
from planner.notifications.runtime import NotificationLoop
from planner.runtime.worker_step_readiness_loop import WorkerStepReadinessLoop
from planner.server_lifecycle.application import ChangeStreamClosingServer


class RecordingWebPush:
    def __init__(self, intents: list[dict[str, Any]]) -> None:
        self.intents = intents

    def send(
        self,
        subscription: PushSubscription,
        intent: NotificationIntent,
        identity: WebPushIdentity,
        *,
        subject: str,
    ) -> WebPushResult:
        self.intents.append({"transport": "web", "intent": asdict(intent)})
        return WebPushResult(delivered=True)


class RecordingAPNs:
    def __init__(self, intents: list[dict[str, Any]]) -> None:
        self.intents = intents

    def send(
        self, delivery: PendingAPNsDelivery, *, server_url: str, now: int,
    ) -> APNsResult:
        self.intents.append({"transport": "apns", "intent": asdict(delivery.intent)})
        return APNsResult(delivered=True)

    def close(self) -> None:
        pass


class AwaitedReadinessLoop(WorkerStepReadinessLoop):
    """Keep completed futures too, so a fast failure cannot escape the HTTP answer."""

    completed: list[concurrent.futures.Future[Any]]

    def _step_ended(self, future: concurrent.futures.Future[Any]) -> None:
        self.completed.append(future)
        super()._step_ended(future)


class AwaitedManagerWakeLoop(ManagerWakeLoop):
    completed: list[concurrent.futures.Future[Any]]

    def _delivery_ended(self, future: concurrent.futures.Future[bool]) -> None:
        self.completed.append(future)
        super()._delivery_ended(future)


async def _drain(loop: AwaitedReadinessLoop | AwaitedManagerWakeLoop) -> None:
    while True:
        with loop._in_flight_lock:
            pending = tuple(loop._in_flight)
        if not pending:
            break
        await asyncio.gather(*(asyncio.wrap_future(future) for future in pending))
    for future in loop.completed:
        future.result()
    loop.completed.clear()


def build_app(config: Config) -> FastAPI:
    assert config.test_mode, "The comparison host requires test mode"
    clock = build_clock(config)

    def make_child(
        *, resolved_start: Any, event_sink: BackendEventSink, message_files: Any,
    ) -> ScriptedChild:
        # Turns remain open until cancellation. No real provider executes a prompt.
        return ScriptedChild(event_sink)

    patch(
        "planner.conversation.snapshot.SubprocessBackendProbeEnvironment", NoInstalledBackends,
    ).start()
    app = create_app(
        config,
        clock,
        lambda: connect(config.db_path),
        backend_child_factories_for_test={key: make_child for key in ConversationBackendKey},
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with original_lifespan(application):
            snapshots = TicketModelSnapshots()
            snapshots.model = "gpt-6.1-sol"
            snapshots.models = ("gpt-6.1-sol",)
            object.__setattr__(app.state.conversation, "backend_snapshots", snapshots)
            yield

    app.router.lifespan_context = lifespan
    readiness: AwaitedReadinessLoop | None = None
    wakes: AwaitedManagerWakeLoop | None = None
    notification_intents: list[dict[str, Any]] = []
    notifications = NotificationLoop(
        config.db_path,
        clock,
        canonical_origin="https://comparison.invalid",
        adapter=RecordingWebPush(notification_intents),
        apns_adapter=RecordingAPNs(notification_intents),
    )
    sources = SourceScheduleLoop(
        config.db_path, clock, participants=build_participants(config, clock),
    )
    pass_lock = asyncio.Lock()

    @app.post("/api/test/poll/readiness")
    async def poll_readiness() -> dict[str, list[str]]:
        nonlocal readiness
        async with pass_lock:
            if readiness is None:
                readiness = AwaitedReadinessLoop(
                    config.db_path, clock,
                    conversation_system=app.state.conversation_system,
                    asyncio_loop=asyncio.get_running_loop(),
                )
                readiness.completed = []
            ids = await asyncio.to_thread(readiness.poll_once)
            await _drain(readiness)
            return {"ticket_ids": sorted(ids)}

    @app.post("/api/test/poll/manager-wakes")
    async def poll_manager_wakes() -> dict[str, list[int]]:
        nonlocal wakes
        async with pass_lock:
            if wakes is None:
                wakes = AwaitedManagerWakeLoop(
                    config.db_path, clock,
                    conversation_system=app.state.conversation_system,
                    asyncio_loop=asyncio.get_running_loop(),
                )
                wakes.completed = []
            ids = await asyncio.to_thread(wakes.poll_once)
            await _drain(wakes)
            return {"batch_ids": sorted(ids)}

    @app.post("/api/test/poll/notifications")
    async def poll_notifications() -> dict[str, Any]:
        async with pass_lock:
            notification_intents.clear()
            delivered = await asyncio.to_thread(notifications.poll_once)
            return {"delivered": delivered, "intents": list(notification_intents)}

    @app.post("/api/test/poll/sources")
    async def poll_sources() -> dict[str, bool]:
        async with pass_lock:
            await asyncio.to_thread(sources.poll_once)
            return {"complete": True}

    return app


def main() -> None:
    config = load_config("config.yaml")
    app = build_app(config)
    listener_fd = int(os.environ["PLAN_SERVER_LISTENER_FD"])
    ChangeStreamClosingServer(uvicorn.Config(app, fd=listener_fd)).run()


if __name__ == "__main__":
    main()
