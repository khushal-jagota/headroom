"""t_tt03 — the CLI/API ingress validates each position against the ticket's OWN
type, not a global enum.

Drives both a ``coding`` ticket (byte-identical to today) and a ``probe`` ticket
through the in-process API, asserting the exact validation codes per type:
- a field foreign to the type is rejected with the type-scoped payload;
- a ceiling / state foreign to the type is rejected with the right code;
- a coding ticket still accepts coding fields/states (no regression);
- a probe proposal parks on the field the registry gates for its state.
Plus the ``?state=`` filter decision (reserved bookends cross-type; a non-reserved
state requires ticket_type).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


@pytest.fixture
def probe_installed() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


@pytest.fixture
def app_db(tmp_path: Path):
    db_path = tmp_path / "ingress.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_GATEWAY_ADAPTER": "fake", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), build_adapters(config), conn_factory), db_path


def _create(client: TestClient, ticket_type: str) -> str:
    r = client.post(
        "/api/tickets", json={"title": ticket_type, "type": ticket_type, "kickoff_note": "k"}
    )
    assert r.status_code == 200, r.json()
    return r.json()["id"]


# --- coding ingress stays byte-identical ---------------------------------------


def test_coding_ticket_accepts_coding_field_and_ceiling(app_db, probe_installed: None) -> None:
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


def test_coding_ticket_rejects_probe_field(app_db, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "coding")
        # probe's alpha field is foreign to coding.
        r = client.post(f"/api/tickets/{tid}/propose/alpha", json={"body": "x"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation"
        assert r.json()["error"]["message"] == "unknown ticket field"
        assert r.json()["error"]["detail"] == {"field": "alpha", "type_id": "coding"}


def test_coding_ticket_rejects_probe_ceiling(app_db, probe_installed: None) -> None:
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
    app_db, probe_installed: None
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
        assert bad_field.json()["error"]["detail"] == {"field": "success", "type_id": "probe"}
        # A foreign next_ceiling (coding's needs_plan) on the probe kickoff accept is
        # rejected against probe's ceiling range.
        bad_ceiling = client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "needs_plan", "at_cap": "stop"},
        )
        assert bad_ceiling.status_code == 400
        assert bad_ceiling.json()["error"]["code"] == "scope_invalid"
        assert bad_ceiling.json()["error"]["detail"] == {"next_ceiling": "needs_plan"}


# --- probe ingress: per-type field / ceiling / state ---------------------------


def test_probe_proposal_parks_on_registry_selected_field(app_db, probe_installed: None) -> None:
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


def test_probe_rejects_coding_field_value_edit(app_db, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        r = client.put(f"/api/tickets/{tid}/value/success", json={"body": "x"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation"
        assert r.json()["error"]["detail"] == {"field": "success", "type_id": "probe"}


def test_probe_rejects_coding_state_and_note_field(app_db, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        state = client.post(f"/api/tickets/{tid}/state", json={"to": "needs_plan"})
        assert state.status_code == 400
        assert state.json()["error"]["message"] == "state outside the linear order"
        note = client.put(
            f"/api/tickets/{tid}/notes/plan", json={"user_note": "x"}
        )
        assert note.status_code == 400
        assert note.json()["error"]["detail"] == {"field": "plan", "type_id": "probe"}


def test_probe_accepts_its_own_state_via_direct_state(app_db, probe_installed: None) -> None:
    # /state must stop rejecting probe stages: advancing directly to needs_beta from
    # needs_alpha is a valid probe stage jump (kickoff first settled so it's non-kickoff).
    app, _db = app_db
    with TestClient(app) as client:
        tid = _create(client, "probe")
        client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "done", "at_cap": "propose"},
        )
        jumped = client.post(
            f"/api/tickets/{tid}/state", json={"to": "needs_beta"}
        )
        assert jumped.status_code == 200, jumped.json()
        assert jumped.json()["state"] == "needs_beta"


# --- the ?state= filter decision -----------------------------------------------


def test_state_filter_reserved_bookends_need_no_type(app_db, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        _create(client, "coding")
        _create(client, "probe")
        for reserved in ("needs_kickoff", "done", "dropped"):
            r = client.get(f"/api/tickets?state={reserved}")
            assert r.status_code == 200, r.json()
        # Both fresh tickets sit at needs_kickoff, so that bookend returns both.
        both = client.get("/api/tickets?state=needs_kickoff").json()["tickets"]
        assert len(both) == 2


def test_state_filter_non_reserved_requires_type(app_db, probe_installed: None) -> None:
    app, _db = app_db
    with TestClient(app) as client:
        _create(client, "coding")
        # A non-reserved state with no ticket_type is ambiguous -> validation.
        r = client.get("/api/tickets?state=needs_success")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation"
        assert r.json()["error"]["detail"] == {"state": "needs_success"}
        # With the right type it validates and filters. Advance the coding ticket to
        # needs_success so the filter returns exactly it.
        made = client.get("/api/tickets?state=needs_kickoff&ticket_type=coding").json()["tickets"]
        assert len(made) == 1
        tid = made[0]["id"]
        client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "needs_success", "at_cap": "stop"},
        )
        ok = client.get("/api/tickets?state=needs_success&ticket_type=coding")
        assert ok.status_code == 200, ok.json()
        assert [t["id"] for t in ok.json()["tickets"]] == [tid]
        # A state foreign to the named type -> validation.
        foreign = client.get("/api/tickets?state=needs_alpha&ticket_type=coding")
        assert foreign.status_code == 400
        assert foreign.json()["error"]["message"] == "state outside the linear order"
