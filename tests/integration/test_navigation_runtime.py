"""Publication admission uses isolated synthetic state and the real runtime lease."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def make_app(tmp_path: Path, *, test_mode: bool = True) -> FastAPI:
    path = tmp_path / "planner.db"
    if not path.exists():
        conn = connect(str(path))
        create_schema(conn)
        from tests.support.model_catalogue import seed_test_model_catalogue

        seed_test_model_catalogue(conn)
        conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
        },
    )
    if not test_mode:
        from dataclasses import replace

        config = replace(config, test_mode=False, dispatch_enabled=True)
    return create_app(
        config,
        build_clock(config),
        lambda: connect(str(path)),
        conversation_system_for_test=InMemoryConversationSystem() if test_mode else None,
    )
