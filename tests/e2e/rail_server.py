"""Isolated rail browser server with deterministic conversation contract answers.

The real durable message record remains active. The in-memory system supplies one
pending question to exercise ask-only presentation without invoking an agent backend.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import uvicorn
from tests.support.model_catalogue import seed_test_model_catalogue

from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import MessageContent
from planner.conversation.storage import ConversationStore
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import Principal
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.server_lifecycle.application import ChangeStreamClosingServer


class RailConversations(InMemoryConversationSystem):
    def __init__(self, store: ConversationStore) -> None:
        super().__init__()
        self.store = store

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        # Browser snapshot routes read the durable record, as the production core writes.
        # The in-memory contract alone intentionally stores no conversation rows.
        await super().start_conversation(request)
        await self.store.create_conversation(resolve_conversation_start_request(request))

    async def record_message_to_owner(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        sender: Principal,
        recipient: Principal,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> None:
        # A message to the owner never invokes a backend. Preserve the normal durable
        # record door while the test double supplies deterministic activity/ask facts.
        await self.store.append_event(
            conversation_id,
            MessageToOwnerEventPayload(
                content=content,
                sender=sender,
                recipient=recipient,
                sender_label=sender_label,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            ),
        )

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return conversation_id == "rail-question"


def main() -> None:
    config = load_config("config.yaml")
    assert config.test_mode
    assert Path(config.db_path).name == "rail-proof.db"
    with closing(connect(config.db_path)) as bootstrap:
        create_schema(bootstrap)
        seed_test_model_catalogue(bootstrap)

    def conn_factory() -> sqlite3.Connection:
        return connect(config.db_path)

    app = create_app(
        config,
        build_clock(config),
        conn_factory,
        conversation_system_for_test=RailConversations(ConversationStore(config.db_path)),
    )
    ChangeStreamClosingServer(uvicorn.Config(app, host="127.0.0.1", port=config.port)).run()


if __name__ == "__main__":
    main()
