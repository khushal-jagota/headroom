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
from dataclasses import asdict, fields, is_dataclass
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI
from tests.compare.scripted.record_inspection import record_rows
from tests.compare.scripted.scripted_child import decode_scripted_event
from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry
from tests.e2e.conversation_signals_server import NoInstalledBackends
from tests.e2e.ticket_model_controls import TicketModelSnapshots
from tests.support.in_memory_conversation_system import InMemoryConversationSystem

from planner.conversation import contracts
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.message_content import (
    MessageFile,
    MessageImage,
    MessageText,
    message_content_from_stored,
    text_message_content,
)
from planner.core.clock import build_clock
from planner.core.config import Config, load_config
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
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
        self,
        delivery: PendingAPNsDelivery,
        *,
        server_url: str,
        now: int,
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


def _in_memory_snapshot(value: Any) -> Any:
    if isinstance(value, (MessageText, MessageImage, MessageFile)):
        kind = {MessageText: "text", MessageImage: "image", MessageFile: "file"}[type(value)]
        return {"piece": kind, **asdict(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _in_memory_snapshot(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_in_memory_snapshot(item) for item in value]
    if isinstance(value, dict):
        return {key: _in_memory_snapshot(item) for key, item in value.items()}
    return value


def _in_memory_routes(app: FastAPI, system: InMemoryConversationSystem) -> None:
    """Expose only the reference fake controls and observations to request lists."""

    @app.post("/api/test/in-memory-conversations/start")
    async def start(body: dict[str, Any]) -> dict[str, bool]:
        values = dict(body)
        if values.get("workspace_folder") is not None:
            values["workspace_folder"] = Path(values["workspace_folder"])
        if values.get("role_materials") is not None:
            values["role_materials"] = contracts.ConversationRoleMaterials(
                **values["role_materials"]
            )
        if values.get("backend_key") is not None:
            values["backend_key"] = contracts.ConversationBackendKey(values["backend_key"])
        if values.get("access") is not None:
            values["access"] = contracts.ConversationAccess(values["access"])
        if "workspace_path_policy" in values:
            values["workspace_path_policy"] = contracts.WorkspacePathPolicy(
                values["workspace_path_policy"]
            )
        await system.start_conversation(contracts.ConversationStartRequest(**values))
        return {"started": True}

    @app.post("/api/test/in-memory-conversations/{conversation_id}/send")
    async def send(conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        options = dict(body)
        content = message_content_from_stored(options)
        options.pop("text", None)
        options.pop("content", None)
        if "mode" in options:
            options["mode"] = contracts.PromptDeliveryMode(options["mode"])
        if "recovery_references" in options:
            options["recovery_references"] = tuple(
                contracts.DeploymentRecoveryReference(**value)
                for value in options["recovery_references"]
            )
        for name in ("sender", "recipient"):
            if options.get(name) is not None:
                options[name] = Principal(**options[name])
        fate = await system.send(conversation_id, content, **options)
        names = {
            contracts.PromptDeliveryStarted: "started",
            contracts.PromptDeliveryQueued: "queued",
            contracts.PromptDeliveryInjected: "injected",
            contracts.PromptDeliveryRefused: "refused",
            contracts.PromptDeliveryUncertain: "uncertain",
            contracts.PromptDeliverySuppressed: "suppressed",
        }
        return {"fate": names[type(fate)], **asdict(fate)}

    @app.get("/api/test/in-memory-conversations/{conversation_id}/state")
    async def state(conversation_id: str) -> dict[str, Any]:
        return {
            "running": await system.is_running(conversation_id),
            "workspace_activity": system.workspace_activity(),
            "held_prompts": _in_memory_snapshot(await system.held_prompts(conversation_id)),
            "observations": _in_memory_snapshot(system.observations(conversation_id)),
            "backend_prompt_writes": _in_memory_snapshot(
                system.backend_prompt_writes(conversation_id)
            ),
        }

    @app.post("/api/test/in-memory-conversations/{conversation_id}/complete")
    async def complete(conversation_id: str) -> dict[str, bool]:
        system.complete_running_turn(conversation_id)
        return {"complete": True}


def _scripted_routes(app: FastAPI, registry: ScriptedBackendRegistry, db_path: str) -> None:
    children = registry.current
    """Drive the existing scripted child through the real Python core for shared seeds."""

    async def settled() -> None:
        await app.state.conversation.system.wait_until_quiescent()

    @app.post("/api/test/signals/{conversation_id}/finish")
    async def finish(conversation_id: str) -> dict[str, bool]:
        await children[conversation_id].finish()
        await settled()
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/questions")
    async def questions(conversation_id: str) -> dict[str, bool]:
        await children[conversation_id].request_questions()
        await settled()
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/reject-next-prompt")
    async def reject_next_prompt(conversation_id: str) -> dict[str, bool]:
        children[conversation_id].reject_next_prompt = True
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/message")
    async def message(conversation_id: str, body: dict[str, str]) -> dict[str, bool]:
        await app.state.conversation.system.record_message_to_owner(
            conversation_id,
            text_message_content(body.get("text", "Explicit message to owner")),
            sender_label=body["label"],
            sender=Principal(PrincipalKind(body["kind"]), body["id"]),
            recipient=OWNER_PRINCIPAL,
        )
        await settled()
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/backend-news")
    async def backend_news(conversation_id: str, body: dict[str, Any]) -> dict[str, int]:
        child = children[conversation_id]
        assert child.token is not None
        aliases = {
            "tool_call_started": "ToolCallStarted",
            "tool_call_finished": "ToolCallFinished",
            "plan_updated": "PlanUpdated",
            "token_usage": "TokenUsageReported",
            "context_compacted": "ContextCompacted",
            "command_reply": "CommandReplyReported",
            "user_input_failed": "UserInputFailed",
            "turn_ended": "TurnEnded",
            "permission_asked": "PermissionAskRaised",
        }
        for scripted in body["events"]:
            fields = dict(scripted)
            kind = fields.pop("kind")
            values = fields.get("values", fields)
            if kind == "permission_asked":
                values = {"ask": {"detail": None, **values}}
            news = decode_scripted_event({"kind": aliases.get(kind, kind), "values": values})
            await child.emit(news, turn=child.token)
        await child.flush_events()
        await settled()
        return {"reported": len(body["events"])}

    @app.post("/api/test/signals/{conversation_id}/configure")
    async def configure(conversation_id: str, body: dict[str, Any]) -> dict[str, bool]:
        await registry.configure(conversation_id, body["command"], body["behavior"])
        return {"configured": True}

    @app.get("/api/test/signals/{conversation_id}/inspect")
    async def inspect(conversation_id: str) -> dict[str, Any]:
        await settled()
        children = await registry.inspect(conversation_id)
        system = app.state.conversation.system
        runtime = {
            "running": await system.is_running(conversation_id),
            "held_prompts": _in_memory_snapshot(await system.held_prompts(conversation_id)),
            "pending_permission_ask_ids": sorted(
                await system.pending_permission_ask_ids(conversation_id)
            ),
            "pending_user_input_request_ids": sorted(
                await system.pending_user_input_request_ids(conversation_id)
            ),
        }
        return {
            **record_rows(db_path, conversation_id),
            "runtime": runtime,
            "backend": {"children": children},
        }

    @app.post("/api/test/conversation-steering")
    async def steering(body: dict[str, list[str]]) -> dict[str, bool]:
        keys = frozenset(contracts.ConversationBackendKey(key) for key in body["backend_keys"])
        patch.object(contracts, "BACKEND_KEYS_SUPPORTING_STEER", keys).start()
        return {"configured": True}


def build_app(config: Config) -> FastAPI:
    assert config.test_mode, "The comparison host requires test mode"
    clock = build_clock(config)
    registry = ScriptedBackendRegistry()
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem

    # Constructor seams receive the shared clock before reconciliation or autonomous work.
    patch(
        "planner.conversation.runtime_composition.ConversationStore",
        partial(ConversationStore, integer_now=clock.now_unix),
    ).start()
    patch(
        "planner.conversation.runtime_composition.SqliteProcessConversationSystem",
        partial(SqliteProcessConversationSystem, unix_time_now=clock.now_unix),
    ).start()

    patch(
        "planner.conversation.snapshot.SubprocessBackendProbeEnvironment",
        NoInstalledBackends,
    ).start()
    in_memory = (
        InMemoryConversationSystem()
        if os.environ.get("PLAN_COMPARE_CONVERSATION_SYSTEM") == "in_memory"
        else None
    )
    app = create_app(
        config,
        clock,
        lambda: connect(config.db_path),
        backend_child_factories_for_test={
            key: registry.make_child for key in ConversationBackendKey
        },
        conversation_system_for_test=in_memory,
    )
    if in_memory is not None:
        _in_memory_routes(app, in_memory)
    else:
        _scripted_routes(app, registry, config.db_path)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            async with original_lifespan(application):
                snapshots = TicketModelSnapshots()
                snapshots.model = "gpt-6.1-sol"
                snapshots.models = ("gpt-6.1-sol",)
                object.__setattr__(app.state.conversation, "backend_snapshots", snapshots)
                yield
        finally:
            await registry.close()

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
        config.db_path,
        clock,
        participants=build_participants(config, clock),
    )
    pass_lock = asyncio.Lock()

    @app.post("/api/test/poll/readiness")
    async def poll_readiness() -> dict[str, list[str]]:
        nonlocal readiness
        async with pass_lock:
            if readiness is None:
                readiness = AwaitedReadinessLoop(
                    config.db_path,
                    clock,
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
                    config.db_path,
                    clock,
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
