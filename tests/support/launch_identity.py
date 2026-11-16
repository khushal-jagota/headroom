"""Explicit authenticated agent fixtures, using canonical links and real issuance."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.server import ServerHandle

from planner.agent_identity.data import authenticate_launch_token, issue_launch_token
from planner.core.db import connect

_TOKENS: dict[tuple[str, str], str] = {}
_PRINCIPAL_ALIASES: dict[tuple[str, str, str], str] = {}


def agent_headers(
    source: sqlite3.Connection | str | Path | FastAPI | TestClient | ServerHandle,
    actor: str,
    identity: str | None = None,
    *,
    conversation_id: str | None = None,
    owner_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Issue for a real fixture principal; retired actor names stay refused.

    This helper is called explicitly at each authenticated fixture construction. It
    neither interprets incoming requests nor replaces the authentication boundary.
    Missing Worker/Manager fixture aliases get real unrelated domain records, so
    their denial checks still exercise the intended principal kind.
    """
    connected_server = source if isinstance(source, ServerHandle) else None
    if isinstance(source, ServerHandle):
        source = source.db_path
    test_client = source if isinstance(source, TestClient) else None
    if isinstance(source, TestClient):
        source = cast(FastAPI, source.app)
    if isinstance(source, FastAPI):
        config = getattr(source.state, "config", None)
        if config is None:
            with closing(source.state.conn_factory()) as temporary:
                source = str(temporary.execute("PRAGMA database_list").fetchone()[2])
        else:
            source = config.db_path
    owns_connection = not isinstance(source, sqlite3.Connection)
    conn = connect(str(source)) if owns_connection else source
    assert isinstance(conn, sqlite3.Connection)
    try:
        database = str(conn.execute("PRAGMA database_list").fetchone()[2])
        if actor in {"worker", "manager"} and identity:
            alias_key = (database, actor, identity)
            identity = _PRINCIPAL_ALIASES.get(alias_key, identity)
            table = "tickets" if actor == "worker" else "managers"
            if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (identity,)).fetchone() is None:
                if actor == "worker":
                    from planner.core.contracts import OWNER_PRINCIPAL
                    from planner.tickets.data import create_ticket

                    fixture = create_ticket(
                        conn,
                        title="Authenticated unrelated fixture Worker",
                        principal=OWNER_PRINCIPAL,
                        now=1,
                        title_max_chars=200,
                        worker_type="coding",
                    )
                else:
                    from planner.core.clock import RealClock
                    from planner.managers.data import create_manager

                    manager_fixture = create_manager(
                        conn,
                        title="Authenticated unrelated fixture Manager",
                        project_id="project_vylo",
                        clock=RealClock(),
                    )
                identity = fixture.id if actor == "worker" else manager_fixture.id
                _PRINCIPAL_ALIASES[alias_key] = identity
        if actor == "worker" and identity:
            row = conn.execute(
                "SELECT conversation_id FROM tickets WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                return {"Authorization": "Bearer invalid-fixture-principal"}
            current = row[0]
        elif actor == "chief":
            identity = "chief_of_staff"
            row = conn.execute(
                "SELECT conversation_id FROM agents WHERE agent_key=?", (identity,)
            ).fetchone()
            current = None if row is None else row[0]
        elif actor == "manager" and identity:
            if conn.execute("SELECT 1 FROM managers WHERE id=?", (identity,)).fetchone() is None:
                return {"Authorization": "Bearer invalid-fixture-principal"}
            identity = "manager_" + identity
            row = conn.execute(
                "SELECT conversation_id FROM agents WHERE agent_key=?", (identity,)
            ).fetchone()
            current = None if row is None else row[0]
        else:
            return {"Authorization": "Bearer invalid-fixture-principal"}
        cid = conversation_id or current or "fixture-identity-" + uuid4().hex
        if current is not None and cid != current:
            raise AssertionError("identity fixture must not replace a current conversation")
        database = str(conn.execute("PRAGMA database_list").fetchone()[2])
        key = (database, cid)
        if key in _TOKENS:
            # Fixture requests made after a scripted child respawns need the newly
            # issued credential. A retired conversation must stay retired.
            if current == cid and authenticate_launch_token(conn, _TOKENS[key]) is None:
                _TOKENS[key] = issue_launch_token(conn, cid)
            return {"Authorization": "Bearer " + _TOKENS[key]}
        if (
            conn.execute("SELECT 1 FROM conversations WHERE conversation_id=?", (cid,)).fetchone()
            is None
        ):
            if connected_server is not None:
                started = httpx.post(
                    connected_server.base + "/api/conversation/conversations",
                    json={"conversation_id": cid, "backend_key": "codex", "model": "gpt-6.1-sol"},
                    headers=owner_headers,
                    timeout=10,
                )
                assert started.status_code == 201, started.text
            elif test_client is not None and test_client.portal is not None:
                from tests.support.in_memory_conversation_system import InMemoryConversationSystem

                from planner.conversation.contracts import (
                    ConversationAlreadyStarted,
                    ConversationStartRequest,
                )

                system = getattr(cast(FastAPI, test_client.app).state, "conversation_system", None)
                if isinstance(system, InMemoryConversationSystem):
                    try:
                        test_client.portal.call(
                            system.start_conversation,
                            ConversationStartRequest(conversation_id=cid, model="gpt-6.1-sol"),
                        )
                    except ConversationAlreadyStarted:
                        # In-memory starts need not persist the production row.
                        pass
            if connected_server is None:
                conn.execute(
                    "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
                    "identity_environment_variables,access,created_at) VALUES(?,?,?,?,?,?,?)",
                    (cid, "codex", "gpt-6.1-sol", str(Path.cwd()), "[]", "full", 1),
                )
        if actor == "worker":
            conn.execute(
                "INSERT OR IGNORE INTO ticket_conversations(conversation_id,ticket_id) VALUES(?,?)",
                (cid, identity),
            )
            conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?", (cid, identity))
        else:
            conn.execute(
                "INSERT INTO agents(agent_key,conversation_id) VALUES(?,?) "
                "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
                (identity, cid),
            )
        conn.commit()
        _TOKENS[key] = issue_launch_token(conn, cid)
        return {"Authorization": "Bearer " + _TOKENS[key]}
    finally:
        if owns_connection:
            conn.close()


def agent_environment(
    source: sqlite3.Connection | str | Path | FastAPI | TestClient | ServerHandle,
    actor: str,
    identity: str | None = None,
    *,
    conversation_id: str | None = None,
    owner_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = agent_headers(
        source, actor, identity, conversation_id=conversation_id, owner_headers=owner_headers
    )
    return {"PLAN_LAUNCH_TOKEN": headers["Authorization"].removeprefix("Bearer ")}
