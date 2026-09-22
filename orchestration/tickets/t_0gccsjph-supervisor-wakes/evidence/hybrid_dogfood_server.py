"""Sanitized harness used by the authoritative Codex Worker dogfood.

Ticket-role conversations use the production backend factory. Other roles use the
repository scripted backend child so the manager incumbent can be held and completed
deterministically. All paths and ports come from PLAN_* environment variables.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import HTTPException

from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ConversationBackendKey,
    PromptDeliveryUncertain,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.production_backends import production_backend_child_factories
from planner.core import server as server_module
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.loops import start_background_loops

adapter_spec = importlib.util.spec_from_file_location(
    "wake_dogfood_adapter",
    Path(__file__).resolve().parents[4] / "tests/unit/test_conversation_system.py",
)
assert adapter_spec is not None and adapter_spec.loader is not None
adapter_module = importlib.util.module_from_spec(adapter_spec)
sys.modules[adapter_spec.name] = adapter_module
adapter_spec.loader.exec_module(adapter_module)
FakeBackend = adapter_module._FakeBackend
FakeBackendChild = adapter_module._FakeBackendChild

config = load_config()
real_factories = production_backend_child_factories(
    panels_server_url=f"http://127.0.0.1:{config.port}"
)
scripted_backends: dict[str, Any] = {}


def hybrid_child(
    *,
    resolved_start: ResolvedConversationStart,
    event_sink: BackendEventSink,
    message_files: ConversationMessageFiles,
) -> Any:
    identity = dict(
        resolved_start.role_materials.identity_environment_variables
        if resolved_start.role_materials is not None
        else ()
    )
    if "PLAN_TICKET_ID" in identity:
        return real_factories[resolved_start.backend_key](
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
        )
    backend = scripted_backends.setdefault(
        resolved_start.conversation_id, FakeBackend(resolved_start.conversation_id)
    )
    return FakeBackendChild(backend, event_sink)


class ArmedConversationSystem:
    """Delegate everything except one armed Worker opener's uncertain fate."""

    def __init__(self) -> None:
        self.inner: Any = None
        self.armed_ticket_id: str | None = None
        self.armed_conversation_id: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def start_conversation(self, request: Any) -> None:
        await self.inner.start_conversation(request)
        identity = dict(
            request.role_materials.identity_environment_variables
            if request.role_materials is not None
            else ()
        )
        if (
            self.armed_ticket_id is not None
            and identity.get("PLAN_TICKET_ID") == self.armed_ticket_id
        ):
            self.armed_conversation_id = request.conversation_id

    async def send(self, conversation_id: str, content: Any, **kwargs: Any) -> Any:
        if conversation_id == self.armed_conversation_id:
            self.armed_ticket_id = None
            self.armed_conversation_id = None
            return PromptDeliveryUncertain()
        return await self.inner.send(conversation_id, content, **kwargs)

    async def send_with_receipt(
        self, conversation_id: str, content: Any, **kwargs: Any
    ) -> AddressedPromptDeliveryReceipt:
        if conversation_id == self.armed_conversation_id:
            self.armed_ticket_id = None
            self.armed_conversation_id = None
            return AddressedPromptDeliveryReceipt(
                fate=PromptDeliveryUncertain(), newly_accepted=True
            )
        return await self.inner.send_with_receipt(conversation_id, content, **kwargs)


system = ArmedConversationSystem()
loops: Any = None
server_module.production_backend_child_factories = lambda **_kwargs: {
    key: hybrid_child for key in ConversationBackendKey
}
with connect(config.db_path, config.db_busy_timeout_ms) as schema_connection:
    create_schema(schema_connection)
app = server_module.create_app(
    config,
    build_clock(config),
    lambda: connect(config.db_path, config.db_busy_timeout_ms),
    conversation_system_for_test=system,
)
configured_lifespan = app.router.lifespan_context


@asynccontextmanager
async def dogfood_lifespan(application: Any) -> Any:
    global loops
    async with configured_lifespan(application):
        system.inner = application.state.conversation.system
        if os.environ.get("DOGFOOD_AUTO_LOOPS") == "1":
            loops = start_background_loops(
                config,
                build_clock(config),
                conversation_system=system,
                asyncio_loop=asyncio.get_running_loop(),
            )
        try:
            yield
        finally:
            if loops is not None:
                await loops.stop()
                loops = None


app.router.lifespan_context = dogfood_lifespan


@app.post("/dogfood/arm-worker-error/{ticket_id}")
async def arm_worker_error(ticket_id: str) -> dict[str, object]:
    system.armed_ticket_id = ticket_id
    system.armed_conversation_id = None
    return {"armed_ticket_id": ticket_id}


@app.post("/dogfood/start-loops")
async def start_loops() -> dict[str, object]:
    global loops
    if loops is not None:
        return {"started": False}
    loops = start_background_loops(
        config,
        build_clock(config),
        conversation_system=system,
        asyncio_loop=asyncio.get_running_loop(),
    )
    return {"started": True}


@app.post("/dogfood/stop-loops")
async def stop_loops() -> dict[str, object]:
    global loops
    if loops is None:
        return {"stopped": False}
    await loops.stop()
    loops = None
    return {"stopped": True}


@app.post("/dogfood/complete/{conversation_id}")
async def complete(conversation_id: str) -> dict[str, object]:
    backend = scripted_backends.get(conversation_id)
    if backend is None or backend.live_turn_token is None or backend.sink is None:
        raise HTTPException(409, "conversation has no active scripted turn")
    token = backend.live_turn_token
    backend.live_turn_token = None
    await backend.sink.turn_ended(
        token,
        ending=ConversationTurnEnding.completed,
        error_summary=None,
        standard_error_tail=None,
    )
    return {"completed": True, "cancellations": backend.cancellations}


@app.get("/dogfood/backend/{conversation_id}")
async def backend_state(conversation_id: str) -> dict[str, object]:
    backend = scripted_backends.get(conversation_id)
    if backend is None:
        return {"exists": False}
    return {
        "exists": True,
        "running": backend.live_turn_token is not None,
        "cancellations": backend.cancellations,
        "writes": [write.text for write in backend.writes],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="info")
