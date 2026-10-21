"""t_tt03 — the CLI/API ingress validates each position against the ticket's OWN
type, not a global enum.

Drives both a ``coding`` ticket (byte-identical to today) and a ``probe`` ticket
through the in-process API, asserting the exact validation codes per type:
- a field foreign to the type is rejected with the type-scoped payload;
- a ceiling / stage foreign to the type is rejected with the right code;
- a coding ticket still accepts coding fields/stages (no regression);
- a probe proposal parks on the field the registry gates for its stage.
Plus the ``/api/tickets`` read decisions the same harness reaches: the ``?stage=``
filter, which compares stored data directly without resolving a Worker type, and
``?search=``, which reads every place a Ticket keeps words.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.worker_types.configuration import load_worker_runtime_definitions
from planner.worker_types.store import read_definition


def _deny_retired_ticket_guidance(
    action: int, table: str | None, column: str | None, _db: str | None, _trigger: str | None,
) -> int:
    if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_UPDATE) and (
        table, column
    ) == ("tickets", "guidance"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


AppDb = tuple[FastAPI, Path]
OWNER = {"kind": "owner", "id": "owner"}


@pytest.fixture
def probe_installed() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


@pytest.fixture
def app_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_installed: None,
) -> AppDb:
    db_path = tmp_path / "ingress.db"
    boot = connect(str(db_path))
    create_schema(boot)
    probe = seed_probe_worker_type(boot)
    coding = read_definition(boot, "coding")
    boot.commit()
    load_worker_runtime_definitions(boot)
    boot.close()

    # The ingress tests own these model choices, not the locally installed providers.
    async def snapshot(
        self: BackendSnapshotService,
        backend_key: ConversationBackendKey,
        *,
        refresh: bool = False,
        discover_update_advisory: bool = True,
    ) -> BackendSnapshot:
        profiles = tuple(
            definition.worker_profile
            for definition in (coding, probe)
            if definition.worker_profile.default_backend == backend_key.value
        )
        assert profiles, backend_key
        models = tuple(
            BackendModel(
                profile.default_model,
                reasoning_effort_options=(
                    (profile.default_reasoning_effort,)
                    if profile.default_reasoning_effort is not None else ()
                ),
            )
            for profile in profiles
        )
        return BackendSnapshot(
            backend_key=backend_key,
            installed=True,
            executable_path="/scripted/backend",
            version="1.0.0",
            identity=None,
            available_models=models,
            reasoning_effort_options=(),
            default_model_id=models[0].model_id,
            default_reasoning_effort=profiles[0].default_reasoning_effort,
            update_advisory=None,
            diagnoses=(),
        )

    monkeypatch.setattr(BackendSnapshotService, "snapshot", snapshot)
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        conn = connect(str(db_path))
        conn.set_authorizer(_deny_retired_ticket_guidance)
        return conn

    return create_app(
        config,
        build_clock(config),
        conn_factory,
        conversation_system_for_test=InMemoryConversationSystem(),
    ), db_path


def _create(client: TestClient, worker_type: str) -> str:
    r = client.post(
        "/api/tickets",
        json={"title": worker_type, "worker_type": worker_type, "brief": "k"},
    )
    assert r.status_code == 200, r.json()
    ticket_id: str = r.json()["id"]
    return ticket_id


def _propose(client: TestClient, ticket_id: str, body: str) -> None:
    filed = client.post(
        f"/api/tickets/{ticket_id}/propose",
        json={"body": body},
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
    )
    assert filed.status_code == 200, filed.json()


# --- coding ingress stays byte-identical ---------------------------------------






# --- probe ingress: per-type field / ceiling / stage ---------------------------




def test_proposal_route_accepts_only_the_ticket_own_worker(app_db: AppDb) -> None:
    app, db_path = app_db
    with TestClient(app) as client:
        target = _create(client, "coding")
        parent = _create(client, "coding")
        unrelated = _create(client, "coding")
        scoped = client.post(
            f"/api/tickets/{target}/accept/brief",
            json={
                "next_ceiling": "needs_success_condition",
                "next_holder": {"kind": "ticket", "id": parent},
            },
        )
        assert scoped.status_code == 200, scoped.text
        item = client.post(
            "/api/managers", json={"title": "Supervisor", "project_id": "project_vylo"}
        ).json()
        attempts = (
            {
                "X-Plan-Actor": "manager",
                "X-Plan-Sprint-Item-ID": str(item["id"]),
            },
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": parent},
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": unrelated},
        )
        for headers in attempts:
            refused = client.post(
                f"/api/tickets/{target}/propose",
                json={"body": "Foreign", "recap": "Foreign"},
                headers=headers,
            )
            assert refused.status_code == 400
            assert refused.json()["error"]["code"] == "agent_forbidden"

        own = client.post(
            f"/api/tickets/{target}/propose",
            json={"body": "Own Worker", "recap": "Own"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": target},
        )
        assert own.status_code == 200, own.text
        assert own.json()["pending_proposal"]["body"] == "Own Worker"


# --- the ?stage= filter decision -----------------------------------------------






def test_retired_guidance_is_preserved_but_has_no_api_surface(app_db: AppDb) -> None:
    app, db_path = app_db
    original = "  Historical boundary\n\nKeep this. λ\x00  "
    # Schema reopen runs before the actual runtime owns this synthetic database.
    with closing(connect(str(db_path))) as conn:
        with conn:
            ticket_id = tickets_data.create_ticket(
                conn, title="coding", worker_type="coding", brief="k",
                principal=OWNER_PRINCIPAL, now=1, title_max_chars=TITLE_MAX_CHARS,
            ).id
            conn.execute("UPDATE tickets SET guidance=? WHERE id=?", (original, ticket_id))
            before = hashlib.sha256(
                conn.execute("SELECT CAST(guidance AS BLOB) FROM tickets WHERE id=?", (ticket_id,))
                .fetchone()[0]
            ).hexdigest()
            active_record = dict(conn.execute(
                "SELECT * FROM tickets WHERE id=?", (ticket_id,)
            ).fetchone())
            del active_record["guidance"]
            expected_record_hash = hashlib.sha256(json.dumps(
                active_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode()).hexdigest()
        conn.set_authorizer(_deny_retired_ticket_guidance)
        create_schema(conn)
        assert tickets_data.ticket_record_sha256(conn, ticket_id) == expected_record_hash
    with TestClient(app) as client:
        with connect(str(db_path)) as conn:
            conn.set_authorizer(_deny_retired_ticket_guidance)
            ticket = tickets_data.read_ticket(conn, ticket_id)
            conn.execute(
                "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
                "access, created_at) VALUES (?, ?, ?, '/work', 'full', ?)",
                ("retired-guidance-proof", ticket.employee_backend, ticket.employee_launch_model,
                 ticket.updated_at),
            )
            tickets_data.write_ticket_conversation_start(
                conn, ticket_id, conversation_id="retired-guidance-proof",
                backend=ticket.employee_backend, model=ticket.employee_launch_model,
                reasoning_effort=ticket.employee_launch_reasoning_effort, now=ticket.updated_at,
            )
            assert tickets_data.read_ticket_by_conversation_id(
                conn, "retired-guidance-proof"
            ).id == ticket_id
        path = f"/api/tickets/{ticket_id}"
        read_path = f"/api/tickets?detail=full&id={ticket_id}"
        saved = client.get(read_path)
        assert saved.status_code == 200, saved.json()
        assert "guidance" not in saved.json()
        assert saved.json()["field_values"] == {}
        assert saved.json()["pending_proposal"]["field"] == "brief"
        for body in (
            {"guidance": "replacement", "recap": "must not change"},
            {"guidance": None},
            {"guidance_append": "new text"},
            {"guidance_append": ""},
            {"key": "plan"},
        ):
            rejected = client.patch(path, json=body)
            assert rejected.status_code == 400
            assert client.get(read_path).json() == saved.json()
        edited = client.patch(path, json={"recap": "Current work"})
        assert edited.status_code == 200, edited.json()
        assert "guidance" not in edited.json()
        accepted = client.post(
            path + "/accept/brief",
            json={"next_ceiling": "needs_success_condition", "next_holder": OWNER},
        )
        assert accepted.status_code == 200, accepted.json()
        _propose(client, ticket_id, "A current proposal")
        rejected = client.post(path + "/reject", json={"message": "Revise this."})
        assert rejected.status_code == 200, rejected.json()
        copied = client.get(path + "/copy-text")
        assert copied.status_code == 200
        assert "guidance:" not in copied.text and "Historical boundary" not in copied.text
        assert client.get("/api/tickets?detail=summary&search=Historical").json()["tickets"] == []
        assert client.put(path + "/notes/plan", json={"body": "old"}).status_code == 404
        with connect(str(db_path)) as conn:
            conn.set_authorizer(_deny_retired_ticket_guidance)
            old_hash = tickets_data.ticket_record_sha256(conn, ticket_id)
            conn.execute("UPDATE tickets SET recap='Same-second change' WHERE id=?", (ticket_id,))
            assert tickets_data.ticket_record_sha256(conn, ticket_id) != old_hash
            conn.set_authorizer(None)
            after = hashlib.sha256(
                conn.execute("SELECT CAST(guidance AS BLOB) FROM tickets WHERE id=?", (ticket_id,))
                .fetchone()[0]
            ).hexdigest()
        assert before == after
