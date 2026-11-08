"""The running server rebuilds a stored view left at an older projection version.

This is the check on the ``VIEW_PROJECTION_VERSION`` contract. A read refuses a view at any
other version, so a bump is safe only while something brings old views up to date. Two
bumps once shipped with nothing that did, and every older transcript stopped showing. If the
server stops rebuilding out-of-date views after startup, this test fails.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ResolvedConversationStart,
)
from planner.conversation.events import AgentMessageEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _seed_conversation_with_an_old_view(db_path: Path) -> None:
    store = ConversationStore(str(db_path))

    async def seed() -> None:
        await store.create_conversation(
            ResolvedConversationStart(
                conversation_id="c",
                backend_key=ConversationBackendKey.hermes,
                model="a-model",
                reasoning_effort=None,
                role_materials=None,
                workspace_folder=Path("/tmp/workspace"),
                access=ConversationAccess.full,
            )
        )
        await store.append_event(
            "c", AgentMessageEventPayload(content=text_message_content("An answer from before"))
        )

    asyncio.run(seed())
    with connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE conversation_view_state SET projection_version=?",
            (VIEW_PROJECTION_VERSION - 1,),
        )


def test_the_server_rebuilds_a_view_left_at_an_older_version(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir()
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    _seed_conversation_with_an_old_view(db_path)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))

    with TestClient(app) as client:
        deadline = time.monotonic() + 10
        while True:
            response = client.get("/api/conversation/conversations/c/view", params={"lens": "full"})
            if response.status_code != 409 or time.monotonic() > deadline:
                break
            time.sleep(0.05)

    assert response.status_code == 200, response.text
    assert "An answer from before" in response.text
    assert response.json()["page"]["projection_version"] == VIEW_PROJECTION_VERSION


def test_an_event_that_arrives_while_a_view_is_computed_is_caught_up(tmp_path: Path) -> None:
    """The view is computed without the write lock, so an event can arrive meanwhile."""
    from contextlib import closing

    from planner.conversation.views import rebuild

    db_path = tmp_path / "planning.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    _seed_conversation_with_an_old_view(db_path)
    with closing(connect(str(db_path))) as conn:
        rebuild._create_private_view_tables(conn)
        computed_through = rebuild.compute_conversation_view(conn, "c")
        later = asyncio.run(
            ConversationStore(str(db_path)).append_event(
                "c", AgentMessageEventPayload(content=text_message_content("Arrived meanwhile"))
            )
        )
        rebuild.write_conversation_view(conn, "c", computed_through)
        assert tuple(
            conn.execute(
                "SELECT projection_version,through_sequence FROM main.conversation_view_state"
            ).fetchone()
        ) == (VIEW_PROJECTION_VERSION, later.sequence)
        assert (
            conn.execute(
                "SELECT row_kind FROM main.conversation_view_rows WHERE sequence=?",
                (later.sequence,),
            ).fetchone()[0]
            == "agent_message"
        )


def test_failed_rebuilds_are_retried_in_the_same_run_and_what_is_left_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from planner.conversation.views import rebuild
    from planner.conversation.workspace_admission import WorkspaceAdmission

    attempts: list[str] = []

    def rebuild_one(db_path: str, busy_timeout_ms: int, conversation_id: str) -> None:
        attempts.append(conversation_id)
        if conversation_id == "broken" or attempts.count(conversation_id) == 1:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(rebuild, "out_of_date_conversation_ids", lambda *_: ["flaky", "broken"])
    monkeypatch.setattr(rebuild, "rebuild_one_conversation_view", rebuild_one)
    monkeypatch.setattr(rebuild, "_FIRST_RETRY_WAIT_SECONDS", 0.0)
    with caplog.at_level(logging.ERROR, logger=rebuild.__name__):
        asyncio.run(rebuild.rebuild_out_of_date_views("unused.db", 5000, WorkspaceAdmission()))

    assert attempts.count("flaky") == 2
    assert attempts.count("broken") == rebuild._RETRY_ROUNDS + 1
    assert [record.getMessage() for record in caplog.records] == [
        f"Conversation views left out of date after {rebuild._RETRY_ROUNDS + 1} attempts: broken"
    ]
