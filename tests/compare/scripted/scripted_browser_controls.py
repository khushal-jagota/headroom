"""Original browser controls, driven by the same actual scripted process as comparisons."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from fastapi import FastAPI
from tests.e2e.connected_controls import install_connected_controls

from planner.conversation.contracts import ConversationRoleMaterials, ConversationStartRequest
from planner.conversation.message_content import text_message_content
from planner.core.clock import Clock
from planner.core.db import connect
from planner.tickets import data as ticket_data

from .scripted_registry import ScriptedBackendRegistry


def install_browser_controls(
    app: FastAPI, registry: ScriptedBackendRegistry, db_path: str, clock: Clock
) -> None:
    install_connected_controls(app, db_path)

    @app.post("/api/test/signals/{conversation_id}/start")
    async def start(conversation_id: str, body: dict[str, str] | None = None) -> dict[str, bool]:
        ticket_id = (body or {}).get("ticket_id")
        system = app.state.conversation.system
        if await app.state.conversation.store.read_conversation(conversation_id) is None:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id,
                    model="gpt-6.1-sol",
                    role_materials=(
                        None
                        if ticket_id is None
                        else ConversationRoleMaterials(
                            role_text="Bounded synthetic Ticket worker for the browser proof.",
                            identity_environment_variables=(
                                ("PLAN_ACTOR", "worker"),
                                ("PLAN_TICKET_ID", ticket_id),
                            ),
                        )
                    ),
                )
            )
        if ticket_id is not None:
            with closing(connect(db_path)) as connection:
                ticket_data.write_ticket_conversation_start(
                    connection,
                    ticket_id,
                    conversation_id=conversation_id,
                    backend="codex",
                    model="gpt-6.1-sol",
                    reasoning_effort=None,
                    now=clock.now_unix(),
                )
                connection.commit()
        await system.send(
            conversation_id,
            text_message_content("Bounded proof turn"),
            sender_label="Owner",
        )
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/queue")
    async def queue(conversation_id: str) -> dict[str, bool]:
        for number in range(1, 3):
            await app.state.conversation.system.send(
                conversation_id,
                text_message_content(f"queued message {number}"),
                sender_label="owner",
                sender_message_id=f"sender-{number}",
            )
        return {"ok": True}

    @app.get("/api/test/signals/{conversation_id}/answers")
    async def answers(conversation_id: str) -> dict[str, Any]:
        snapshots = await registry.inspect(conversation_id)
        latest = snapshots[-1] if snapshots else {"answers": {}}
        return {
            request_id: {answer["question_id"]: {"answers": answer["answers"]} for answer in values}
            for request_id, values in latest["answers"].items()
        }

    @app.get("/api/test/signals/{conversation_id}/delivered")
    async def delivered(conversation_id: str) -> dict[str, list[str]]:
        return {
            "texts": [
                text
                for snapshot in await registry.inspect(conversation_id)
                for text in snapshot["delivered_text"]
            ]
        }
