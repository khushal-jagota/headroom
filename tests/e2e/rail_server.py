"""Isolated rail browser server with deterministic conversation contract answers.

The real durable message record remains active. The in-memory system supplies one
pending question to exercise ask-only presentation without invoking an agent backend.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import uvicorn

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.server_lifecycle.application import ChangeStreamClosingServer


class RailConversations(InMemoryConversationSystem):
    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return conversation_id == "rail-question"


def main() -> None:
    config = load_config("config.yaml")
    assert config.test_mode
    assert Path(config.db_path).name == "rail-proof.db"
    with connect(config.db_path) as bootstrap:
        create_schema(bootstrap)

    def conn_factory() -> sqlite3.Connection:
        return connect(config.db_path)

    app = create_app(
        config, build_clock(config), conn_factory, conversation_system_for_test=RailConversations()
    )
    ChangeStreamClosingServer(uvicorn.Config(app, host="127.0.0.1", port=config.port)).run()


if __name__ == "__main__":
    main()
