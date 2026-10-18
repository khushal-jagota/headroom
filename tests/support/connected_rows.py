"""Durable conversation fixtures and public Worker proposal requests."""

from __future__ import annotations

import httpx
from tests.support.server import JsonObject, ServerHandle, _append_rows

from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.message_content import text_message_content
from planner.core.contracts import OWNER_PRINCIPAL, Principal


def _post(server: ServerHandle, path: str, body: JsonObject) -> JsonObject:
    response = httpx.post(server.base + path, json=body, timeout=10)
    assert response.is_success, response.text
    value: JsonObject = response.json()
    return value


def _conversation(server: ServerHandle, name: str) -> None:
    _post(
        server,
        "/api/conversation/conversations",
        {"conversation_id": name, "model": "gpt-6.1-sol", "backend_key": "codex"},
    )


def _message(server: ServerHandle, conversation: str, sender: Principal, text: str) -> None:
    _append_rows(
        server,
        conversation,
        MessageToOwnerEventPayload(
            content=text_message_content(text),
            sender=sender,
            recipient=OWNER_PRINCIPAL,
            sender_label="Proof Worker",
        ),
    )


def _propose(server: ServerHandle, ticket_id: str, body: str) -> None:
    response = httpx.post(
        server.base + f"/api/tickets/{ticket_id}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
        json={"body": body, "recap": "The next result is ready for review."},
        timeout=10,
    )
    assert response.is_success, response.text
