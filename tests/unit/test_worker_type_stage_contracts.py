"""AD01's public Worker-type and stored-Stage contract."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from sqlite3 import Connection
from typing import get_type_hints

from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli.main import main
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import Ticket
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

_EMPTY_FIELDS_DEFAULT = (
    '{"kickoff":{"value":null,"proposal":null,"user_note":null},'
    '"success":{"value":null,"proposal":null,"user_note":null},'
    '"approach":{"value":null,"proposal":null,"user_note":null},'
    '"plan":{"value":null,"proposal":null,"user_note":null},'
    '"implementation":{"value":null,"proposal":null,"user_note":null},'
    '"closeout":{"value":null,"proposal":null,"user_note":null}}'
)


def _app(tmp_path: Path):
    db_path = tmp_path / "worker-type-stage.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_GATEWAY_ADAPTER": "fake", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), build_adapters(config), conn_factory)


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
    assert list(CODING_WORKER_TYPE_DEFINITION.stage_ids()) + [
        CODING_WORKER_TYPE_DEFINITION.dropped_stage.id
    ] == [
        "needs_kickoff",
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
        "dropped",
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
    assert columns["fields"][3] == 1
    assert columns["fields"][4] is None
    assert "ticket_type" not in columns
    assert "state" not in columns
    assert "employee_session_id" in columns
    assert columns["employee_session_id"][3] == 0
    assert columns["employee_session_id"][4] is None
    assert "chat_session_key" not in columns
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(tickets)")}
    assert "idx_tickets_stage" in indexes
    assert "idx_tickets_worker_type_stage" in indexes
    assert "idx_tickets_state" not in indexes
    assert "idx_tickets_type_state" not in indexes


def _create_almost_v20_ticket_table(
    conn: Connection,
    *,
    recap_definition: str = "TEXT NOT NULL DEFAULT ''",
    ceiling_definition: str = "TEXT NOT NULL",
) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        f"""
        CREATE TABLE tickets (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL CHECK (length(title) <= 200),
          worker_type TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT 'needs_kickoff',
          priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
          deadline TEXT,
          project_id TEXT REFERENCES projects(id),
          sprint_item_id TEXT REFERENCES sprint_items(id),
          sprint_id TEXT REFERENCES sprints(id),
          recap {recap_definition},
          ceiling {ceiling_definition},
          at_cap TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
          ticket_status TEXT NOT NULL DEFAULT 'empty'
            CHECK (ticket_status IN ('empty','agent_running_step','awaiting_approval',
                                     'user_takeover','errored')),
          implementer TEXT CHECK (implementer IN ('khushal','panels_worker',
                                                  'hermes_codex','hermes_claude')),
          employee_session_id TEXT,
          alias TEXT,
          fields TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        INSERT INTO tickets (
          id, title, worker_type, stage, ceiling, fields, created_at, updated_at
        ) VALUES (
          't_incomplete', 'Incomplete', 'coding', 'needs_success', 'needs_success',
          '{_EMPTY_FIELDS_DEFAULT}', 1, 1
        );
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")


def test_incomplete_target_constraints_are_rebuilt(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "incomplete.db"))
    _create_almost_v20_ticket_table(
        conn, ceiling_definition="TEXT NOT NULL DEFAULT 'needs_success'"
    )
    create_schema(conn)
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert columns["ceiling"][4] is None
    row = conn.execute(
        "SELECT worker_type, stage, ceiling, fields FROM tickets WHERE id = 't_incomplete'"
    ).fetchone()
    assert row is not None
    assert tuple(row[:3]) == ("coding", "needs_success", "needs_success")
    assert '"kickoff"' in row["fields"]


def test_incomplete_non_ceiling_constraint_is_rebuilt(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "incomplete-recap.db"))
    _create_almost_v20_ticket_table(conn, recap_definition="TEXT DEFAULT ''")
    create_schema(conn)
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert columns["recap"][3] == 1
    row = conn.execute(
        "SELECT worker_type, stage, ceiling FROM tickets WHERE id = 't_incomplete'"
    ).fetchone()
    assert row is not None
    assert tuple(row) == ("coding", "needs_success", "needs_success")


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
        assert made.json()["stage"] == "needs_kickoff"
        assert "ticket_type" not in made.json()
        assert "state" not in made.json()
        manifest = client.get("/api/worker-types")
        assert manifest.status_code == 200
        assert [item["worker_type"] for item in manifest.json()["worker_types"]] == [
            "coding",
            "new_worker",
            "exploration",
        ]
        assert client.get("/api/ticket-types").status_code == 404

        list_parameters = {
            item["name"]
            for item in client.get("/openapi.json").json()["paths"]["/api/tickets"]["get"][
                "parameters"
            ]
        }
        assert "stage" in list_parameters
        assert "worker_type" not in list_parameters


def test_ticket_list_help_has_stage_without_worker_type_disambiguation() -> None:
    result = CliRunner().invoke(main, ["ticket", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "--stage" in result.output
    assert "--worker-type" not in result.output
