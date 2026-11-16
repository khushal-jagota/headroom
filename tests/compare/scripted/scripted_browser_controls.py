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

    review_departures: dict[str, list[dict[str, Any]]] = {}
    departure_trace_installed = False

    def batch_rows(identity: str) -> list[dict[str, Any]]:
        with closing(connect(db_path)) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id,status,sender_message_id FROM manager_wake_batches "
                    "WHERE conversation_id=? ORDER BY id",
                    (identity,),
                )
            ]

    @app.get("/api/test/signals/{conversation_id}/review-batch")
    async def review_batch_state(conversation_id: str) -> dict[str, Any]:
        return {
            "departures": review_departures.get(conversation_id, []),
            "batches": batch_rows(conversation_id),
        }

    @app.post("/api/test/signals/{conversation_id}/review-batch")
    async def review_batch(conversation_id: str) -> dict[str, Any]:
        nonlocal departure_trace_installed
        review_departures.setdefault(conversation_id, [])
        if not departure_trace_installed:
            store = app.state.conversation.store
            original = store.mark_held_sender_messages_leaving_queue

            async def traced(identity: str, senders: tuple[str, ...]) -> int:
                if identity not in review_departures:
                    return int(await original(identity, senders))
                before = batch_rows(identity)
                changed = await original(identity, senders)
                review_departures.setdefault(identity, []).append(
                    {"before": before, "changed": changed, "after": batch_rows(identity)}
                )
                return int(changed)

            store.mark_held_sender_messages_leaving_queue = traced
            departure_trace_installed = True
        # Claim a real proposal batch without dispatch, so the list owns dispatch timing.
        from dataclasses import asdict

        from planner.manager_wakes import data as wakes

        with closing(connect(db_path)) as connection:
            batch = wakes.claim_next_batch(
                connection, process_token="unit15b", now=clock.now_unix()
            )
            if batch is None:
                raise ValueError("No review batch to claim")
            wakes.record_batch_offering(
                connection,
                batch.id,
                conversation_id=conversation_id,
                process_token="unit15b",
                now=clock.now_unix(),
            )
            return asdict(batch)

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
