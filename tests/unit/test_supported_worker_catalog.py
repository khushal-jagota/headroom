"""New work uses the supported catalog; historical work keeps its definitions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_scheduled_tickets import _template

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.scheduled_tickets import actions as schedule_actions
from planner.scheduled_tickets import data as schedule_data
from planner.scheduled_tickets.contracts import ScheduleCadence
from planner.tickets import actions, data
from planner.worker_types.configuration import load_worker_runtime_definitions
from planner.worker_types.contracts import RETIRED_WORKER_TYPES


def _install_history(conn: Connection) -> None:
    source = Path(__file__).parents[1] / "support" / "retired_worker_types.json"
    for position, definition in enumerate(json.loads(source.read_text()), start=100):
        conn.execute(
            "INSERT OR REPLACE INTO worker_types "
            "(worker_type, position, definition_json, updated_at) VALUES (?, ?, ?, 0)",
            (definition["worker_type"], position, json.dumps(definition)),
        )
    # Definitions may refer to skills absent from a fresh seed. History stores those too.
    for definition in json.loads(source.read_text()):
        skill = definition["profile"]["specialist_skill"]
        conn.execute(
            "INSERT OR IGNORE INTO managed_skills "
            "(skill_name, source_text, updated_at) VALUES (?, ?, 0)",
            (skill, f"---\nname: {skill}\ndescription: Historical specialist\n---\n\nHistory."),
        )
    conn.commit()
    load_worker_runtime_definitions(conn)


@pytest.mark.parametrize("worker_type", sorted(RETIRED_WORKER_TYPES))
def test_retired_types_are_refused_by_all_creation_and_schedule_writers(
    tmp_db: Connection,
    worker_type: str,
) -> None:
    for create in (
        lambda: actions.create_ticket(
            tmp_db,
            title="Retired",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: data.validate_ticket_creation_context(
            tmp_db,
            title="Retired",
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: schedule_actions.create_schedule(
            tmp_db,
            enabled=False,
            cadence=ScheduleCadence.daily,
            local_time="14:30",
            template=_template(worker_type=worker_type),
            now=1,
        ),
    ):
        with pytest.raises(PlannerError, match="retired"):
            create()
    saved = schedule_data.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(worker_type=worker_type),
        now=1,
    )
    for changes in ({"enabled": True}, {"title": "Changed"}):
        with pytest.raises(PlannerError, match="retired"):
            schedule_actions.update_schedule(tmp_db, saved.id, changes, now=2)
    assert (
        schedule_actions.run_current_slot(
            tmp_db,
            planning_now=datetime.fromisoformat("2026-10-16T14:30:00").astimezone(),
            now=3,
            boundary_hour=5,
        )
        == []
    )
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert (
        schedule_actions.update_schedule(
            tmp_db,
            saved.id,
            {"enabled": False},
            now=4,
        ).enabled
        is False
    )
    supported = schedule_data.create_schedule(
        tmp_db,
        enabled=False,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(),
        now=1,
    )
    with pytest.raises(PlannerError, match="retired"):
        schedule_actions.update_schedule(
            tmp_db,
            supported.id,
            {"worker_type": worker_type},
            now=5,
        )


def test_catalog_keeps_historical_manifests_but_omits_them_from_supported_config(
    tmp_path: Path,
) -> None:
    db = tmp_path / "catalog.db"
    with connect(str(db)) as conn:
        create_schema(conn)
        _install_history(conn)
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db)})
    app = create_app(
        cfg,
        build_clock(cfg),
        lambda: connect(str(db)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        history = {
            row["worker_type"]: row
            for row in client.get("/api/worker-types").json()["worker_types"]
        }
        supported = client.get("/api/worker-types?supported=true").json()["worker_types"]
        assert RETIRED_WORKER_TYPES <= history.keys()
        assert all(history[name]["creatable"] is False for name in RETIRED_WORKER_TYPES)
        assert not RETIRED_WORKER_TYPES.intersection(row["worker_type"] for row in supported)
        assert not RETIRED_WORKER_TYPES.intersection(
            row["worker_type"] for row in client.get("/api/workers").json()["workers"]
        )
        for name in RETIRED_WORKER_TYPES:
            assert client.get(f"/api/worker-types/{name}").status_code == 200
            response = client.post("/api/tickets", json={"title": "Retired", "worker_type": name})
            assert response.status_code == 400
        unknown = client.post(
            "/api/tickets", json={"title": "Unknown", "worker_type": "unknown"}
        )
        assert unknown.status_code == 400
        assert not RETIRED_WORKER_TYPES.intersection(
            unknown.json()["error"]["detail"]["worker_types"]
        )
        unknown_schedule = client.post(
            "/api/schedules",
            json={"title": "Unknown", "worker_type": "unknown", "local_time": "14:30"},
        )
        assert unknown_schedule.status_code == 400
        assert not RETIRED_WORKER_TYPES.intersection(
            unknown_schedule.json()["error"]["detail"]["worker_types"]
        )
        ticket = client.post(
            "/api/tickets", json={"title": "Supported", "worker_type": "coding"}
        ).json()
        for name in RETIRED_WORKER_TYPES:
            assert (
                client.patch(f"/api/tickets/{ticket['id']}", json={"worker_type": name}).status_code
                == 400
            )

        # A personal Ticket stored before removal retains its ordinary Brief gate.
        with connect(str(db)) as conn:
            conn.execute("UPDATE tickets SET worker_type='personal' WHERE id=?", (ticket["id"],))
            conn.commit()
        detail = client.get(f"/api/tickets?detail=full&id={ticket['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["worker_type"] == "personal"
        proposal = client.post(
            f"/api/tickets/{ticket['id']}/propose",
            json={"body": "Historical personal brief"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["stage"] == "needs_brief"
        assert proposal.json()["pending_proposal"]["field"] == "brief"
        accepted = client.post(
            f"/api/tickets/{ticket['id']}/accept/brief",
            json={"next_ceiling": "needs_outcome", "next_holder": {"kind": "owner", "id": "owner"}},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["stage"] == "needs_outcome"


def test_cli_worker_type_list_requests_supported_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from planner.cli import http
    from planner.cli.main import main

    def send(method: str, path: str, **_kwargs: object) -> dict[str, object]:
        assert (method, path) == ("GET", "/api/worker-types?supported=true")
        return {"worker_types": [{"worker_type": "coding"}]}

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(main, ["worker-type", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"worker_types": [{"worker_type": "coding"}]}
