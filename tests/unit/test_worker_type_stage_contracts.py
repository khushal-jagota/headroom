"""AD01's public Worker-type and stored-Stage contract."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from sqlite3 import Connection
from typing import get_type_hints

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import build_shipped_registry, shipped_definition

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import Ticket

SHIPPED_REGISTRY = build_shipped_registry()

CODING_WORKER_TYPE_DEFINITION = shipped_definition("coding")

_EMPTY_FIELDS_DEFAULT = (
    '{"brief":{"value":null,"proposal":null},'
    '"success_condition":{"value":null,"proposal":null},'
    '"what_changes":{"value":null,"proposal":null},'
    '"plan":{"value":null,"proposal":null},'
    '"implementation":{"value":null,"proposal":null},'
    '"consequences":{"value":null,"proposal":null}}'
)


def _app(tmp_path: Path) -> FastAPI:
    db_path = tmp_path / "worker-type-stage.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory)


def test_ticket_contract_requires_worker_type_and_stored_stage() -> None:
    ticket_fields = {item.name: item for item in fields(Ticket)}
    assert "worker_type" in ticket_fields
    assert "stage" in ticket_fields
    assert "ticket_type" not in ticket_fields
    assert "state" not in ticket_fields
    assert ticket_fields["worker_type"].default is ticket_fields["stage"].default
    type_hints = get_type_hints(Ticket)
    assert type_hints["worker_type"] is str
    assert type_hints["stage"] is str
    assert list(CODING_WORKER_TYPE_DEFINITION.stage_ids()) == [
        "needs_brief",
        "needs_success_condition",
        "needs_what_changes",
        "needs_plan",
        "needs_implementation",
        "needs_consequences",
        "done",
    ]


def test_fresh_schema_uses_only_worker_type_and_stage(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "schema.db"))
    create_schema(conn)
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "worker_type" in columns
    assert columns["worker_type"][3] == 1
    assert columns["worker_type"][4] is None
    assert "stage" in columns
    assert columns["stage"][3] == 1
    assert columns["stage"][4] == "'needs_kickoff'"
    assert columns["field_values"][3] == 1
    assert columns["field_values"][4] is None
    assert "ticket_type" not in columns
    assert "state" not in columns
    assert "conversation_id" in columns
    assert columns["conversation_id"][3] == 0
    assert columns["conversation_id"][4] is None
    assert "chat_session_key" not in columns
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(tickets)")}
    assert "idx_tickets_stage" in indexes
    assert "idx_tickets_worker_type_stage" in indexes
    assert "idx_tickets_state" not in indexes
    assert "idx_tickets_type_state" not in indexes


def test_http_contract_uses_only_worker_type_and_stage(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        missing = client.post("/api/tickets", json={"title": "Missing"})
        assert missing.status_code == 400
        unknown = client.post("/api/tickets", json={"title": "Unknown", "worker_type": "bogus"})
        assert unknown.status_code == 400
        made = client.post(
            "/api/tickets",
            json={"title": "Coding", "worker_type": "coding", "kickoff_note": "k"},
        )
        assert made.status_code == 200, made.json()
        assert made.json()["worker_type"] == "coding"
        assert made.json()["stage"] == "needs_brief"
        assert "ticket_type" not in made.json()
        assert "state" not in made.json()
        manifest = client.get("/api/worker-types")
        assert manifest.status_code == 200
        assert [item["worker_type"] for item in manifest.json()["worker_types"]] == list(
            SHIPPED_REGISTRY.registered_worker_types()
        )
        assert client.get("/api/ticket-types").status_code == 404

        list_parameters = {
            item["name"]
            for item in client.get("/openapi.json").json()["paths"]["/api/tickets"]["get"][
                "parameters"
            ]
        }
        assert "stage" in list_parameters
        assert "worker_type" not in list_parameters
