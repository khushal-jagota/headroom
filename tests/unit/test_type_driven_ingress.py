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
        "/api/tickets", json={"title": worker_type, "worker_type": worker_type, "kickoff_note": "k"}
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
        # coding's kickoff proposal is parked at create; scope to a coding ceiling works.
        scoped = client.post(
            f"/api/tickets/{tid}/scope",
            json={"ceiling": "needs_approach", "at_cap": "stop"},
        )
        assert scoped.status_code == 200, scoped.json()
        assert scoped.json()["ceiling"] == "needs_approach"


def test_coding_ticket_rejects_probe_field(app_db: AppDb, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "coding")
        # probe's alpha field is foreign to coding.
        r = client.post(f"/api/tickets/{tid}/propose/alpha", json={"body": "x"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation"
        assert r.json()["error"]["message"] == "unknown ticket field"
        assert r.json()["error"]["detail"] == {"field": "alpha", "worker_type": "coding"}


def test_coding_ticket_rejects_probe_ceiling(app_db: AppDb, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "coding")
        r = client.post(
            f"/api/tickets/{tid}/scope",
            json={"ceiling": "needs_alpha", "at_cap": "stop"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "scope_invalid"
        assert r.json()["error"]["detail"] == {"ceiling": "needs_alpha"}


def test_accept_rejects_foreign_field_and_foreign_next_ceiling(
    app_db: AppDb, probe_installed: None
) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        # A probe ticket: accepting a coding field is a per-type field rejection.
        tid = _create(client, "probe")
        bad_field = client.post(
            f"/api/tickets/{tid}/accept/success", json={"next_ceiling": "none", "at_cap": "stop"}
        )
        assert bad_field.status_code == 400
        assert bad_field.json()["error"]["code"] == "validation"
        assert bad_field.json()["error"]["detail"] == {"field": "success", "worker_type": "probe"}
        # A foreign next_ceiling (coding's needs_plan) on the probe kickoff accept is
        # rejected against probe's ceiling range.
        bad_ceiling = client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "needs_plan", "at_cap": "stop"},
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
        # with at_cap=propose so the next propose parks.
        client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "needs_alpha", "at_cap": "propose"},
        )
        # A position-relative propose parks on ALPHA — the field probe's needs_alpha gates.
        parked = client.post(
            f"/api/tickets/{tid}/propose", json={"body": "alpha body", "recap": "r"}
        )
        assert parked.status_code == 200, parked.json()
        assert parked.json()["fields"]["alpha"]["proposal"]["body"] == "alpha body"
        assert parked.json()["fields"]["beta"]["proposal"] is None


def test_probe_rejects_coding_field_value_edit(app_db: AppDb, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        r = client.put(f"/api/tickets/{tid}/value/success", json={"body": "x"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation"
        assert r.json()["error"]["detail"] == {"field": "success", "worker_type": "probe"}


def test_ticket_note_routes_make_replace_and_append_explicit(app_db: AppDb) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "coding")

        replaced = client.put(
            f"/api/tickets/{tid}/notes/plan", json={"user_note": "first guidance"}
        )
        assert replaced.status_code == 200, replaced.json()

        appended = client.post(
            f"/api/tickets/{tid}/notes/plan/append",
            json={"user_note": "second guidance"},
        )
        assert appended.status_code == 200, appended.json()
        assert (
            appended.json()["fields"]["plan"]["user_note"]
            == "first guidance\n\nsecond guidance"
        )

        cleared = client.put(
            f"/api/tickets/{tid}/notes/plan", json={"user_note": None}
        )
        assert cleared.status_code == 200, cleared.json()

        appended_after_clear = client.post(
            f"/api/tickets/{tid}/notes/plan/append",
            json={"user_note": "new guidance"},
        )
        assert appended_after_clear.status_code == 200, appended_after_clear.json()
        assert (
            appended_after_clear.json()["fields"]["plan"]["user_note"]
            == "new guidance"
        )


def test_probe_rejects_coding_state_and_note_field(app_db: AppDb, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        stage = client.post(f"/api/tickets/{tid}/stage", json={"to_stage": "needs_plan"})
        assert stage.status_code == 400
        assert stage.json()["error"]["message"] == "stage outside the linear order"
        note = client.put(
            f"/api/tickets/{tid}/notes/plan", json={"user_note": "x"}
        )
        assert note.status_code == 400
        assert note.json()["error"]["detail"] == {"field": "plan", "worker_type": "probe"}


def test_probe_accepts_its_own_state_via_direct_state(app_db: AppDb, probe_installed: None) -> None:
    # /stage must stop rejecting probe stages: advancing directly to needs_beta from
    # needs_alpha is a valid probe stage jump (kickoff first settled so it's non-kickoff).
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "done", "at_cap": "propose"},
        )
        jumped = client.post(
            f"/api/tickets/{tid}/stage", json={"to_stage": "needs_beta"}
        )
        assert jumped.status_code == 200, jumped.json()
        assert jumped.json()["stage"] == "needs_beta"


# --- the ?stage= filter decision -----------------------------------------------


def test_stage_filter_compares_stored_values_directly(app_db: AppDb, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        _create(client, "coding")
        _create(client, "probe")
        for reserved in ("needs_kickoff", "done", "dropped"):
            r = client.get(f"/api/tickets?stage={reserved}")
            assert r.status_code == 200, r.json()
        # Both fresh tickets sit at needs_kickoff, so that bookend returns both.
        both = client.get("/api/tickets?stage=needs_kickoff").json()["tickets"]
        assert len(both) == 2


def test_stage_filter_non_reserved_needs_no_worker_type(
    app_db: AppDb, probe_installed: None
) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        _create(client, "coding")
        # A non-reserved stage filters directly. Advance the coding ticket to
        # needs_success so the filter returns exactly it.
        made = client.get("/api/tickets?stage=needs_kickoff").json()["tickets"]
        assert len(made) == 1
        tid = made[0]["id"]
        client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "needs_success", "at_cap": "stop"},
        )
        ok = client.get("/api/tickets?stage=needs_success")
        assert ok.status_code == 200, ok.json()
        assert [t["id"] for t in ok.json()["tickets"]] == [tid]
        # An unknown stored value is a valid filter and returns no rows.
        unknown = client.get("/api/tickets?stage=needs_ghost")
        assert unknown.status_code == 200
        assert unknown.json()["tickets"] == []
