"""Ordinary Ticket PATCH is one atomic edit through the real HTTP boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.model_catalogue import seed_test_model_catalogue
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.probe import seed_probe_worker_type

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.scheduled_tickets import data as schedules_data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketTemplate,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
)
from planner.worker_types.configuration import load_worker_runtime_definitions


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    # The probe Worker type is stored like any other, so the app reads it from here.
    seed_probe_worker_type(boot)
    load_worker_runtime_definitions(boot)
    seed_test_model_catalogue(boot)
    boot.commit()
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": "2026-07-10T12:00:00+01:00",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _create_ticket(db_path: Path, **values: Any) -> str:
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type=values.pop("worker_type", "coding"),
            title=values.pop("title", "Before edit"),
            brief=values.pop("brief", "Before note"),
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            **values,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="brief",
            principal=OWNER_PRINCIPAL,
            now=1,
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
        return ticket.id
    finally:
        conn.close()


def test_ticket_block_api_validates_endpoints_duplicates_and_active_cycles(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    first = _create_ticket(db_path, title="First")
    second = _create_ticket(db_path, title="Second")
    third = _create_ticket(db_path, title="Third")
    fourth = _create_ticket(db_path, title="Fourth")
    fifth = _create_ticket(db_path, title="Fifth")

    def add(client: TestClient, blocking: str, blocked: str) -> Any:
        return client.put(f"/api/collections/blockers/{blocked}/{blocking}")

    with TestClient(app) as client:
        missing_blocker = add(client, "t_missing", first)
        missing_blocked = add(client, first, "t_missing")
        self_block = add(client, first, first)
        created = add(client, first, second)
        duplicate = add(client, first, second)
        direct_cycle = add(client, second, first)
        assert add(client, third, fourth).status_code == 200
        assert add(client, fourth, fifth).status_code == 200
        longer_cycle = add(client, fifth, third)

    assert missing_blocker.status_code == 400
    assert missing_blocker.json()["error"] == {
        "code": "ticket_block_invalid",
        "message": "blocking_ticket_id must be an existing ticket",
        "detail": {"blocking_ticket_id": "t_missing"},
    }
    assert missing_blocked.status_code == 400
    assert missing_blocked.json()["error"] == {
        "code": "ticket_block_invalid",
        "message": "blocked_ticket_id must be an existing ticket",
        "detail": {"blocked_ticket_id": "t_missing"},
    }
    assert self_block.status_code == 400
    assert self_block.json()["error"]["code"] == "ticket_block_invalid"
    assert created.json() == {
        "collection": "blockers",
        "container_id": second,
        "member_id": first,
        "ok": True,
    }
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "ticket_block_invalid"
    assert direct_cycle.status_code == 400
    assert direct_cycle.json()["error"]["code"] == "ticket_block_cycle"
    assert longer_cycle.status_code == 400
    assert longer_cycle.json()["error"]["code"] == "ticket_block_cycle"


def _create_pristine_ticket(db_path: Path, *, worker_type: str = "probe") -> str:
    conn = connect(str(db_path))
    try:
        return tickets_data.create_ticket(
            conn,
            worker_type=worker_type,
            title="Pristine backend selection",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        ).id
    finally:
        conn.close()


def _employee_configuration_body(
    employee_backend: str,
    employee_launch_model: str,
    employee_launch_reasoning_effort: str | None = None,
) -> dict[str, object]:
    return {
        "employee_backend": employee_backend,
        "employee_launch_model": employee_launch_model,
        "employee_launch_reasoning_effort": employee_launch_reasoning_effort,
    }


def test_creation_defaults_to_chief_without_calendar_and_preserves_project_context(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        defaulted = client.post("/api/tickets", json={"title": "Default", "worker_type": "coding"})
        project = client.post(
            "/api/tickets",
            json={
                "title": "Project default",
                "worker_type": "coding",
                "project_id": "project_vylo",
                "manager": {"kind": "chief", "id": "chief"},
            },
        )
        for response in (defaulted, project):
            assert response.status_code == 200, response.text
            assert response.json()["manager"] == {"kind": "chief", "id": "chief"}
            assert "sprint_id" not in response.json()
            assert "effective_sprint_id" not in response.json()
            detail = client.get(f"/api/tickets?detail=full&id={response.json()['id']}")
            assert "day_ids" not in detail.json()
        assert project.json()["project_id"] == "project_vylo"
        for response in (defaulted, project):
            assert "priority" not in response.json()
            assert "resolved_priority_anchors" not in response.json()
        with connect(str(db_path)) as conn:
            before = tuple(conn.iterdump())
        for removed in ({"sprint_id": None}, {"sprint_id": "sp_edit"}, {"manager_id": None}):
            rejected = client.post(
                "/api/tickets",
                json={"title": "Obsolete ingress", "worker_type": "coding", **removed},
            )
            assert rejected.status_code == 400
            assert "manager assignment" in rejected.json()["error"]["message"]
        with connect(str(db_path)) as conn:
            assert tuple(conn.iterdump()) == before


def test_parented_ticket_project_is_derived_and_combined_edits_are_atomic(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        vylo_item = client.post(
            "/api/managers", json={"title": "Vylo manager", "project_id": "project_vylo"}
        ).json()
        personal_item = client.post(
            "/api/managers", json={"title": "Personal manager", "project_id": "project_personal"}
        ).json()
        created = client.post(
            "/api/tickets",
            json={
                "title": "Derived project ticket",
                "worker_type": "coding",
                "manager": {"kind": "manager", "id": vylo_item["id"]},
            },
        )
        assert created.status_code == 200, created.text
        ticket_id = str(created.json()["id"])
        assert created.json()["project_id"] == "project_vylo"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                is None
            )

        full = client.get("/api/tickets?detail=full&project_id=project_vylo")
        summary = client.get("/api/tickets?detail=summary&project_id=project_vylo&search=Derived")
        assert ticket_id in {row["id"] for row in full.json()["tickets"]}
        assert ticket_id in {row["id"] for row in summary.json()["tickets"]}

        incompatible = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "manager": {"kind": "manager", "id": personal_item["id"]},
                "project_id": "project_vylo",
            },
        )
        assert incompatible.status_code == 400
        unchanged = client.get(f"/api/tickets?detail=full&id={ticket_id}").json()
        assert unchanged["manager_id"] == vylo_item["id"]
        assert unchanged["project_id"] == "project_vylo"

        with connect(str(db_path)) as conn:
            before_invalid_holder = tuple(conn.iterdump())
        for holder in (
            {"kind": "manager", "id": vylo_item["id"]},
            {"kind": "chief", "id": "chief"},
        ):
            invalid_holder = client.patch(
                f"/api/tickets/{ticket_id}",
                json={
                    "manager": {"kind": "manager", "id": personal_item["id"]},
                    "ceiling_holder": holder,
                    "recap": "Must not persist",
                },
            )
            assert invalid_holder.status_code == 400, invalid_holder.text
            assert invalid_holder.json()["error"]["code"] == "validation"
            assert invalid_holder.json()["error"]["message"] == (
                "ceiling holder must be the user or the Ticket's direct manager; "
                "name an eligible holder explicitly"
            )
            with connect(str(db_path)) as conn:
                assert tuple(conn.iterdump()) == before_invalid_holder

        moved = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "manager": {"kind": "manager", "id": personal_item["id"]},
                "project_id": "project_personal",
                "ceiling_holder": {"kind": "manager", "id": personal_item["id"]},
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["project_id"] == "project_personal"
        assert moved.json()["ceiling_holder"] == {"kind": "manager", "id": personal_item["id"]}
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                is None
            )

        detached = client.patch(
            f"/api/tickets/{ticket_id}", json={"manager": {"kind": "chief", "id": "chief"}}
        )
        assert detached.status_code == 200, detached.text
        assert detached.json()["project_id"] == "project_personal"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                == "project_personal"
            )

        reattached = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"manager": {"kind": "manager", "id": personal_item["id"]}},
        )
        assert reattached.status_code == 200, reattached.text
        replacement = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"manager": {"kind": "chief", "id": "chief"}, "project_id": "project_vylo"},
        )
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["project_id"] == "project_vylo"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                == "project_vylo"
            )

        # A Manager's authorized Project edit keeps only its attached schedule
        # Projects consistent. It grants no control over schedule configuration.
        with connect(str(db_path)) as conn:
            for index, manager in enumerate((personal_item, personal_item, vylo_item)):
                schedule = schedules_data.create_schedule(
                    conn,
                    enabled=index == 0,
                    cadence=ScheduleCadence.daily,
                    local_time="14:30",
                    template=ScheduledTicketTemplate(
                        title=f"Preserved schedule {index}",
                        worker_type="coding",
                        kickoff_note="Preserve this instruction.",
                        deadline="2026-07-31",
                        project_id=manager["project_id"],
                        manager_id=manager["id"],
                        manager_kind="manager",
                        employee_backend="claude",
                        employee_launch_model="sonnet",
                        blocked_by_ticket_ids=(ticket_id,),
                    ),
                    now=1,
                )
                schedules_data.insert_occurrence(
                    conn,
                    schedule_id=schedule.id,
                    occurrence_key="2026-07-09T14:30",
                    target_date="2026-07-09",
                    outcome=OccurrenceOutcome.suppressed,
                    ticket_id=None,
                    error="Historical receipt",
                    now=1,
                )
            before = schedules_data.list_schedules(conn)
            receipts = {row.id: schedules_data.list_occurrences(conn, row.id) for row in before}
        changed_project = client.patch(
            f"/api/managers/{personal_item['id']}",
            json={"project_id": "project_other"},
            headers={"X-Plan-Actor": "manager", "X-Plan-Manager-ID": personal_item["id"]},
        )
        assert changed_project.status_code == 200, changed_project.text
        with connect(str(db_path)) as conn:
            for original in before:
                current = schedules_data.read_schedule(conn, original.id)
                if original.template.manager_id == personal_item["id"]:
                    assert current.updated_at > original.updated_at
                    assert current == replace(
                        original,
                        template=replace(original.template, project_id="project_other"),
                        updated_at=current.updated_at,
                    )
                else:
                    assert current == original
                assert schedules_data.list_occurrences(conn, original.id) == receipts[original.id]


def _backend_snapshot(
    backend_key: str, models: tuple[BackendModel, ...], efforts: tuple[str, ...]
) -> BackendSnapshot:
    return BackendSnapshot(
        backend_key=ConversationBackendKey(backend_key),
        installed=True,
        executable_path=f"/probe/{backend_key}",
        version="1.0.0",
        identity=None,
        available_models=models,
        reasoning_effort_options=efforts,
        default_model_id=models[0].model_id if models else None,
        default_reasoning_effort=efforts[0] if efforts else None,
        update_advisory=None,
        diagnoses=(),
    )
