"""`panels worker my-ticket` uses only the explicit ACP child Ticket identity."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

# --- server: the by-ticket-id worker-self route ------------------------------


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "worker-cli-identity.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(db_path),
    }
    config = load_config(path=None, env=env)
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, clock, conn_factory)
    return app, db_path


def _bind_session(db_path: Path, ticket_id: str, session_key: str) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conversations "
            "(conversation_id, backend_key, model, workspace_folder, access, created_at) "
            "VALUES (?, 'codex', 'model', '/work', 'full', 1)",
            (session_key,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ticket_conversations (conversation_id, ticket_id) "
            "VALUES (?, ?)",
            (session_key, ticket_id),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (session_key, ticket_id),
        )
        conn.commit()
    finally:
        conn.close()






# --- CLI: PLAN_TICKET_ID only ------------------------------------------------


@pytest.fixture
def clear_identity_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("PLAN_TICKET_ID", raising=False)
    yield
