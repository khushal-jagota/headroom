"""t_tt03 — the CLI/API ingress validates each position against the ticket's OWN
type, not a global enum.

Drives both a ``coding`` ticket (byte-identical to today) and a ``probe`` ticket
through the in-process API, asserting the exact validation codes per type:
- a field foreign to the type is rejected with the type-scoped payload;
- a ceiling / stage foreign to the type is rejected with the right code;
- a coding ticket still accepts coding fields/stages (no regression);
- a probe proposal parks on the field the registry gates for its stage.
Plus the ``?stage=`` filter decision: Stage is stored data, so listing compares it
directly without resolving a Worker type.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

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
def app_db(tmp_path: Path) -> AppDb:
    db_path = tmp_path / "ingress.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory), db_path


def _create(client: TestClient, worker_type: str) -> str:
    r = client.post(
        "/api/tickets",
        json={"title": worker_type, "worker_type": worker_type, "kickoff_note": "k"},
    )
    assert r.status_code == 200, r.json()
    ticket_id: str = r.json()["id"]
    return ticket_id


# --- coding ingress stays byte-identical ---------------------------------------


def test_coding_ticket_accepts_coding_field_and_ceiling(
    app_db: AppDb, probe_installed: None
) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "coding")
        # A parked proposal fixes its address, so a direct ceiling edit cannot retarget it.
        scoped = client.patch(f"/api/tickets/{tid}", json={"ceiling": "needs_what_changes"})
        assert scoped.status_code == 400, scoped.json()
        missing_holder = client.post(
            f"/api/tickets/{tid}/accept/brief",
            json={"next_ceiling": "needs_what_changes"},
        )
        assert missing_holder.status_code == 400, missing_holder.json()
        assert missing_holder.json()["error"]["code"] == "scope_missing"
        self_held = client.post(
            f"/api/tickets/{tid}/accept/brief",
            json={
                "next_ceiling": "needs_what_changes",
                "next_holder": {"kind": "ticket", "id": tid},
            },
        )
        assert self_held.status_code == 400, self_held.json()
        assert self_held.json()["error"]["code"] == "validation"
        approved = client.post(
            f"/api/tickets/{tid}/accept/brief",
            json={
                "next_ceiling": "needs_what_changes",
                "next_holder": OWNER,
            },
        )
        assert approved.status_code == 200, approved.json()
        assert approved.json()["ceiling"] == "needs_what_changes"


def test_accept_rejects_foreign_field_and_foreign_next_ceiling(
    app_db: AppDb, probe_installed: None
) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        # A probe ticket: accepting a coding field is a per-type field rejection.
        tid = _create(client, "probe")
        bad_field = client.post(
            f"/api/tickets/{tid}/accept/success_condition",
            json={"next_ceiling": "none"},
        )
        assert bad_field.status_code == 400
        assert bad_field.json()["error"]["code"] == "validation"
        assert bad_field.json()["error"]["detail"] == {
            "field": "success_condition",
            "worker_type": "probe",
        }
        # A foreign next_ceiling (coding's needs_plan) on the probe kickoff accept is
        # rejected against probe's ceiling range.
        bad_ceiling = client.post(
            f"/api/tickets/{tid}/accept/brief",
            json={"next_ceiling": "needs_plan"},
        )
        assert bad_ceiling.status_code == 400
        assert bad_ceiling.json()["error"]["code"] == "scope_invalid"
        assert bad_ceiling.json()["error"]["detail"] == {"next_ceiling": "needs_plan"}


# --- probe ingress: per-type field / ceiling / stage ---------------------------


def test_probe_proposal_parks_on_registry_selected_field(
    app_db: AppDb, probe_installed: None
) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        # kickoff already parked at create — accept it keeping ceiling at needs_alpha
        # so the next answer parks at the ceiling.
        client.post(
            f"/api/tickets/{tid}/accept/brief",
            json={
                "next_ceiling": "needs_alpha",
                "next_holder": OWNER,
            },
        )
        # A position-relative propose parks on ALPHA — the field probe's needs_alpha gates.
        parked = client.post(
            f"/api/tickets/{tid}/propose",
            json={"body": "alpha body", "recap": "r"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": tid},
        )
        assert parked.status_code == 200, parked.json()
        assert parked.json()["pending_proposal"]["body"] == "alpha body"
        assert parked.json()["pending_proposal"]["field"] == "alpha"


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
            "/api/items", json={"title": "Supervisor", "project_id": "project_vylo"}
        ).json()
        attempts = (
            {
                "X-Plan-Actor": "sprint_item_supervisor",
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


def test_stage_filter_non_reserved_needs_no_worker_type(
    app_db: AppDb, probe_installed: None
) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        _create(client, "coding")
        # A non-reserved stage filters directly. Advance the coding ticket to
        # needs_success_condition so the filter returns exactly it.
        made = client.get("/api/tickets?detail=full&stage=needs_brief").json()["tickets"]
        assert len(made) == 1
        tid = made[0]["id"]
        client.post(
            f"/api/tickets/{tid}/accept/brief",
            json={
                "next_ceiling": "needs_success_condition",
                "next_holder": OWNER,
            },
        )
        ok = client.get("/api/tickets?detail=full&stage=needs_success_condition")
        assert ok.status_code == 200, ok.json()
        assert [t["id"] for t in ok.json()["tickets"]] == [tid]
        # An unknown stored value is a valid filter and returns no rows.
        unknown = client.get("/api/tickets?detail=full&stage=needs_ghost")
        assert unknown.status_code == 200
        assert unknown.json()["tickets"] == []


def test_guidance_round_trip_validation_and_retired_field_routes(app_db: AppDb) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        ticket_id = _create(client, "coding")
        path = f"/api/tickets/{ticket_id}"
        read_path = f"/api/tickets?detail=full&id={ticket_id}"
        original = "  Scope boundary\n\nKeep this.  "
        saved = client.patch(path, json={"guidance": original})
        assert saved.status_code == 200
        assert saved.json()["guidance"] == original
        assert saved.json()["field_values"] == {}
        assert saved.json()["pending_proposal"]["field"] == "brief"
        for body in (
            {},
            {"guidance": None},
            {"guidance": 3},
            {"guidance": "lost", "key": "plan"},
            {"key": "plan"},
        ):
            assert client.patch(path, json=body).status_code == 400
            assert client.get(read_path).json()["guidance"] == original
        assert client.patch(path, json={"guidance_append": ""}).json() == saved.json()
        appended = client.patch(path, json={"guidance_append": "\nNew direction  "})
        assert appended.json()["guidance"] == original + "\n\n\nNew direction  "
        assert client.patch(path, json={"guidance": ""}).json()["guidance"] == ""
        assert (
            client.put(f"/api/tickets/{ticket_id}/notes/plan", json={"body": "old"}).status_code
            == 404
        )
