"""Schedule configuration requires authority beyond ordinary Ticket creation."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import CHIEF_PRINCIPAL, OWNER_PRINCIPAL

from planner.core.clock import Clock, build_clock
from planner.core.config import load_config
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.goals.actions import create_goal
from planner.managers import data as managers_data
from planner.scheduled_tickets import actions, api, data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketTemplate,
)
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS

ALLOWED_LOGIN = "schedule-owner@example.com"
CANONICAL_ORIGIN = "https://panels.tailnet.ts.net"


def _app(conn: Connection, *, hosted: bool = False) -> FastAPI:
    database = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    env = {"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(database)}
    if hosted:
        env.update(
            PLAN_TRUSTED_INGRESS_PROVIDER="tailscale",
            PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN=ALLOWED_LOGIN,
            PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN=CANONICAL_ORIGIN,
        )
    config = load_config(path=None, env=env)
    return create_app(config, build_clock(config), lambda: connect(str(database)))


@pytest.fixture
def client(tmp_db: Connection) -> Iterator[TestClient]:
    with TestClient(_app(tmp_db)) as http:
        yield http


@pytest.fixture
def chain(tmp_db: Connection, fake_clock: Clock) -> tuple[str, str, str]:
    own = managers_data.create_manager(
        tmp_db, title="Own Manager", project_id="project_vylo", clock=fake_clock
    )
    unrelated = managers_data.create_manager(
        tmp_db, title="Unrelated Manager", project_id="project_personal", clock=fake_clock
    )
    worker = tickets_actions.create_ticket(
        tmp_db,
        title="Synthetic caller",
        worker_type="coding",
        manager_id=own.id,
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    return own.id, unrelated.id, worker.id


def _template(manager_id: str | None = None) -> ScheduledTicketTemplate:
    return ScheduledTicketTemplate(
        title="Admitted schedule",
        worker_type="coding",
        kickoff_note="Inspect the admitted configuration.",
        deadline=None,
        project_id=None,
        manager_id=manager_id,
        manager_kind="manager" if manager_id is not None else "chief",
        employee_backend=None,
        employee_launch_model=None,
        blocked_by_ticket_ids=(),
    )


def _body(manager_id: str | None = None) -> dict[str, object]:
    return {
        "title": "Admitted schedule",
        "worker_type": "coding",
        "kickoff_note": "Inspect the admitted configuration.",
        "local_time": "14:30",
        "manager": {"kind": "manager", "id": manager_id}
        if manager_id is not None
        else {"kind": "chief", "id": "chief"},
    }


def _snapshot(conn: Connection) -> tuple[tuple[tuple[object, ...], ...], ...]:
    # These rows include all Ticket/schedule/receipt fields and timestamps.
    return tuple(
        tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2"))
        for table in ("scheduled_ticket_schedules", "tickets", "scheduled_ticket_occurrences")
    )


@pytest.mark.parametrize("kind", tuple(PrincipalKind))
def test_canonical_actions_require_schedule_authority(
    tmp_db: Connection, chain: tuple[str, str, str], kind: PrincipalKind
) -> None:
    own, _unrelated, worker = chain
    goal = create_goal(
        tmp_db,
        {"project_id": "project_vylo", "title": "Goal caller", "kind": "binary",
         "primary_checklist_block_id": None},
        principal=OWNER_PRINCIPAL,
        now=1,
    )
    principal = {
        PrincipalKind.owner: OWNER_PRINCIPAL,
        PrincipalKind.chief: CHIEF_PRINCIPAL,
        PrincipalKind.manager: Principal(PrincipalKind.manager, own),
        PrincipalKind.ticket: Principal(PrincipalKind.ticket, worker),
        PrincipalKind.goal: Principal(PrincipalKind.goal, goal.id),
    }[kind]
    # Raw storage supplies history without attributing historical authorship.
    original = data.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(own),
        now=100,
    )
    retired = data.create_schedule(
        tmp_db,
        enabled=False,
        cadence=ScheduleCadence.every_planning_day,
        local_time="05:01",
        template=replace(_template(), worker_type="personal"),
        now=100,
    )
    before = _snapshot(tmp_db)
    if kind in (PrincipalKind.owner, PrincipalKind.chief):
        created = actions.create_schedule(
            tmp_db,
            principal=principal,
            enabled=True,
            cadence=ScheduleCadence.daily,
            local_time="14:30",
            template=_template(own),
            now=100,
        )
        edited = actions.update_schedule(
            tmp_db, created.id, {"title": "Authorized edit"}, principal=principal, now=101
        )
        assert edited.template.title == "Authorized edit"
        assert edited.updated_at == 101
    else:
        with pytest.raises(PlannerError) as denied:
            actions.create_schedule(
                tmp_db,
                principal=principal,
                enabled=True,
                cadence=ScheduleCadence.daily,
                local_time="14:30",
                template=_template(own),
                now=100,
            )
        assert denied.value.code == ErrorCode.agent_forbidden
        for schedule_id, changes in (
            (original.id, {"title": "Unauthorized edit"}),
            (original.id, {"enabled": False}),
            (original.id, {"enabled": True}),
            (original.id, {}),
            (retired.id, {"enabled": False}),
            ("schedule_missing", {}),
        ):
            with pytest.raises(PlannerError) as denied:
                actions.update_schedule(
                    tmp_db, schedule_id, changes, principal=principal, now=102
                )
            assert denied.value.code == ErrorCode.agent_forbidden
        assert _snapshot(tmp_db) == before


def test_canonical_actions_have_no_implicit_owner(tmp_db: Connection) -> None:
    with pytest.raises(TypeError, match="principal"):
        actions.create_schedule(  # type: ignore[call-arg]
            tmp_db,
            enabled=True,
            cadence=ScheduleCadence.daily,
            local_time="14:30",
            template=_template(),
            now=100,
        )
    with pytest.raises(TypeError, match="principal"):
        actions.update_schedule(tmp_db, "schedule_missing", {}, now=100)  # type: ignore[call-arg]
    assert data.list_schedules(tmp_db) == []


@pytest.mark.parametrize(
    "headers",
    (
        {"X-Plan-Actor": "manager"},
        {"X-Plan-Actor": "worker"},
        {
            "X-Plan-Actor": "manager",
            "X-Plan-Manager-ID": "si_one",
            "X-Plan-Sprint-Item-ID": "si_other",
        },
    ),
)
def test_schedule_routes_require_consistent_actor_identity(
    tmp_db: Connection, client: TestClient, headers: dict[str, str]
) -> None:
    before = _snapshot(tmp_db)
    for method, url, body in (
        ("POST", "/api/schedules", _body()),
        ("PATCH", "/api/schedules/schedule_missing", {"title": "Denied"}),
    ):
        response = client.request(method, url, json=body, headers=headers)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "agent_forbidden"
    assert _snapshot(tmp_db) == before


def test_direct_creation_checks_holder_and_denied_schedules_cannot_change_due_work(
    tmp_db: Connection,
    client: TestClient,
    chain: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own, unrelated, worker = chain
    admitted = client.post("/api/schedules", json=_body(own))
    assert admitted.status_code == 200, admitted.text
    schedule_id = admitted.json()["id"]
    disabled = client.post("/api/schedules", json={**_body(own), "enabled": False})
    assert disabled.status_code == 200, disabled.text
    data.insert_occurrence(
        tmp_db,
        schedule_id=schedule_id,
        occurrence_key="2026-07-27T14:30",
        target_date="2026-07-27",
        outcome=OccurrenceOutcome.suppressed,
        ticket_id=None,
        error="Preserved historical receipt",
        now=1,
    )
    tmp_db.commit()
    headers_by_actor = (
        {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": own},
        {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": worker},
    )
    for headers in headers_by_actor:
        direct = {"title": "Direct work", "worker_type": "coding"}
        before_direct = _snapshot(tmp_db)
        direct_response = client.post(
            "/api/tickets",
            json={**direct, "manager": {"kind": "manager", "id": own}},
            headers=headers,
        )
        if headers["X-Plan-Actor"] == "manager":
            assert direct_response.status_code == 200, direct_response.text
            assert direct_response.json()["ceiling_holder"] == {"kind": "manager", "id": own}
        else:
            # Same-chain creation authority does not make a Worker an eligible reviewer.
            assert direct_response.status_code == 400, direct_response.text
            error = direct_response.json()["error"]
            assert error["code"] == "validation"
            assert error["message"] == (
                "ceiling holder must be the user or the Ticket's direct manager; "
                "name an eligible holder explicitly"
            )
            assert _snapshot(tmp_db) == before_direct
        denied = client.post(
            "/api/tickets",
            json={**direct, "manager": {"kind": "manager", "id": unrelated}},
            headers=headers,
        )
        assert denied.status_code == 400, denied.text
        assert denied.json()["error"]["code"] == "agent_forbidden"

    def refuse_template_resolution(*args: object, **kwargs: object) -> None:
        raise AssertionError("Denied callers must not reach template resolution")

    monkeypatch.setattr(api, "_template_from_create", refuse_template_resolution)
    before = _snapshot(tmp_db)
    for headers in headers_by_actor:
        for destination in (own, unrelated):
            denied = client.post("/api/schedules", json=_body(destination), headers=headers)
            assert denied.status_code == 400, denied.text
            assert denied.json()["error"]["code"] == "agent_forbidden"
            assert _snapshot(tmp_db) == before
        enable = client.patch(
            f"/api/schedules/{disabled.json()['id']}", json={"enabled": True}, headers=headers
        )
        assert enable.status_code == 400, enable.text
        assert enable.json()["error"]["code"] == "agent_forbidden"
        assert _snapshot(tmp_db) == before
        for edits in (
            {"enabled": True},
            {"enabled": False},
            {"manager": {"kind": "manager", "id": unrelated}},
            {"title": "Injected title", "kickoff_note": "Injected instruction"},
            {"employee_backend": "claude", "employee_launch_model": "sonnet"},
            {},
            {"unknown": "Denied before body validation"},
        ):
            denied = client.patch(f"/api/schedules/{schedule_id}", json=edits, headers=headers)
            assert denied.status_code == 400, denied.text
            assert denied.json()["error"]["code"] == "agent_forbidden"
            assert _snapshot(tmp_db) == before

    now = datetime.fromisoformat("2026-07-28T14:30:20+02:00")
    occurrences = actions.run_current_slot(tmp_db, local_now=now, now=int(now.timestamp()))
    assert len(occurrences) == 1
    supplied_id = occurrences[0].ticket_id
    assert supplied_id is not None
    supplied = tickets_data.read_ticket(tmp_db, supplied_id)
    assert supplied.title == "Admitted schedule"
    assert supplied.manager_id == own
    assert supplied.project_id == "project_vylo"
    assert supplied.pending_proposal is not None
    assert supplied.pending_proposal.body == "Inspect the admitted configuration."
    # Existing supply tests own receipt idempotence and ordinary approval-gate coverage.


@pytest.mark.parametrize("headers", ({}, {"X-Plan-Actor": "chief"}))
def test_owner_and_chief_can_create_and_edit_schedules(
    client: TestClient, headers: dict[str, str]
) -> None:
    created = client.post("/api/schedules", json=_body(), headers=headers)
    assert created.status_code == 200, created.text
    edited = client.patch(
        f"/api/schedules/{created.json()['id']}",
        json={"title": "Authorized change", "local_time": "15:00", "enabled": False},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Authorized change"
    assert edited.json()["local_time"] == "15:00"
    assert edited.json()["enabled"] is False


def test_schedule_mutations_keep_hosted_login_and_origin_checks(tmp_db: Connection) -> None:
    allowed = {"Tailscale-User-Login": ALLOWED_LOGIN, "Origin": CANONICAL_ORIGIN}
    with TestClient(_app(tmp_db, hosted=True)) as client:
        created = client.post("/api/schedules", json=_body(), headers=allowed)
        assert created.status_code == 200, created.text
        url = f"/api/schedules/{created.json()['id']}"
        before = _snapshot(tmp_db)
        for headers in (
            {**allowed, "Tailscale-User-Login": "unrelated@example.com"},
            {**allowed, "Origin": "https://wrong.example"},
        ):
            for method, target, body in (
                ("POST", "/api/schedules", _body()),
                ("PATCH", url, {"enabled": False}),
            ):
                denied = client.request(method, target, json=body, headers=headers)
                assert denied.status_code == 403, denied.text
                assert denied.json()["error"]["code"] == "agent_forbidden"
                assert _snapshot(tmp_db) == before
        edited = client.patch(url, json={"enabled": False}, headers=allowed)
        assert edited.status_code == 200, edited.text
        assert edited.json()["enabled"] is False
