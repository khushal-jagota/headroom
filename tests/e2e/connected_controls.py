"""Bounded synthetic facts, written by the isolated application's retained owners."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from planner.conversation.message_content import text_message_content
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect


class Binding(BaseModel):
    kind: Literal["ticket", "agent", "feedback"]
    id: str
    conversation_id: str = ""


class OwnerMessage(BaseModel):
    kind: PrincipalKind
    id: str
    text: str


def install_connected_controls(app: FastAPI, db_path: str) -> None:
    """Install only in explicit isolated E2E entry points, never the product app."""

    @app.post("/api/test/connected/bind")
    def bind(body: Binding) -> dict[str, bool]:
        with connect(db_path) as conn:
            if body.kind == "ticket":
                conn.execute(
                    "UPDATE tickets SET conversation_id=? WHERE id=?",
                    (body.conversation_id, body.id),
                )
                conn.execute(
                    "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
                    (body.conversation_id, body.id),
                )
            elif body.kind == "agent":
                conn.execute(
                    "INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) "
                    "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
                    (body.id, body.conversation_id),
                )
            else:
                conn.execute(
                    "UPDATE tickets SET stage='needs_feedback',pending_proposal=NULL WHERE id=?",
                    (body.id,),
                )
        return {"ok": True}

    @app.post("/api/test/connected/{conversation_id}/message")
    async def message(conversation_id: str, body: OwnerMessage) -> dict[str, bool]:
        await app.state.conversation.system.record_message_to_owner(
            conversation_id,
            text_message_content(body.text),
            sender_label="Proof Worker",
            sender=Principal(body.kind, body.id),
            recipient=OWNER_PRINCIPAL,
        )
        return {"ok": True}
