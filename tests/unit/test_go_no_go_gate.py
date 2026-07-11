"""t_tt03 — the go/no-go gate: the falsifiable proof that the CLI/API ingress is
genuinely type-driven.

A ``probe`` ticket (the synthetic SECOND type, ``tests/support/probe.py``) is
created and driven through REAL propose/accept to ``done`` via the in-process
FastAPI ``TestClient`` — real routes -> real marshallers -> per-type parse -> real
writers -> real events. A missed ingress point (a surviving global-enum coercion)
fails HERE, because probe's middle stages (``needs_alpha``/``needs_beta``) and
fields (``alpha``/``beta``) are not ``TicketState``/``FieldName`` members.

Transport note (D-note): the probe registry is installed process-globally via
``set_registry_for_test`` (the probe fixture), which the in-process app resolves
through ``coding_bridge.registry()``. It CANNOT reach a spawned CLI subprocess, so
this gate drives the API layer; the CLI-subprocess proof stays coding-only. This
is a deliberate, correct coverage boundary — the API ingress IS the ingress under
test.

Every assertion is a concrete value: exact state / ceiling / at_cap, the field the
registry selects, the cleared proposal, and the EXACT emitted event kind order.
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

# The exact probe manifest served at /api/ticket-types (mirrors test_probe_type).
PROBE_MANIFEST = {
    "type_id": "probe",
    "label": "Probe",
    "stages": [
        {"id": "needs_kickoff", "label": "Kickoff", "gating_field": "kickoff",
         "is_terminal": False},
        {"id": "needs_alpha", "label": "Alpha", "gating_field": "alpha", "is_terminal": False},
        {"id": "needs_beta", "label": "Beta", "gating_field": "beta", "is_terminal": False},
        {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True},
    ],
    "dropped": {"id": "dropped", "label": "Dropped", "gating_field": None, "is_terminal": True},
    "advance": {"needs_kickoff": "needs_alpha", "needs_alpha": "needs_beta", "needs_beta": "done"},
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "alpha", "label": "Alpha"},
        {"id": "beta", "label": "Beta"},
    ],
    "ceiling_range": ["needs_alpha", "needs_beta", "done"],
    "default_ceiling": "needs_alpha",
    "worker_profile_id": "probe-worker",
}

# The gating accept event order the engine emits for a direct gating accept with an
# onward scope pair (observed once, then pinned): the accept, the linear advance, the
# onward scope change, and the durable-control reset.
_ACCEPT_EVENTS = ["proposal_accepted", "state_changed", "scope_changed", "ticket_status_changed"]
# A parked propose-with-recap: the filed proposal, the recap write, the awaiting reset.
_PROPOSE_EVENTS = ["proposal_filed", "recap_updated", "ticket_status_changed"]


@pytest.fixture
def probe_installed() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


@pytest.fixture
def app_db(tmp_path: Path):
    db_path = tmp_path / "gate.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, build_clock(config), build_adapters(config), conn_factory)
    return app, db_path


def _event_kinds(client: TestClient, tid: str) -> list[str]:
    return [e["kind"] for e in client.get(f"/api/tickets/{tid}/events").json()["events"]]


def test_go_no_go_gate_probe_drives_to_done_through_the_real_api(
    app_db, probe_installed: None
) -> None:
    app, db_path = app_db
    with TestClient(app) as client:
        # 1. Manifest endpoint serves probe's exact shape.
        served = client.get("/api/ticket-types").json()
        probe_manifest = next(m for m in served["types"] if m["type_id"] == "probe")
        assert probe_manifest == PROBE_MANIFEST

        # 1. Create a probe ticket — exact initial state/ceiling/type/fields.
        created = client.post(
            "/api/tickets",
            json={"title": "Probe gate", "type": "probe", "kickoff_note": "kickoff body"},
        )
        assert created.status_code == 200, created.json()
        body = created.json()
        tid = body["id"]
        assert body["state"] == "needs_kickoff"
        assert body["ceiling"] == "needs_alpha"          # probe's first worker stage
        assert body["ticket_type"] == "probe"
        assert list(body["fields"].keys()) == ["kickoff", "alpha", "beta"]
        assert body["fields"]["kickoff"]["proposal"] is not None
        assert body["fields"]["kickoff"]["proposal"]["body"] == "kickoff body"
        assert body["fields"]["alpha"]["value"] is None
        assert body["fields"]["beta"]["value"] is None

        # 4-5. Accept the (create-time) kickoff proposal. Keep the ceiling AT the state
        # advanced into with at_cap=propose, so the next stage's proposal PARKS.
        base = len(_event_kinds(client, tid))
        accept_kickoff = client.post(
            f"/api/tickets/{tid}/accept/kickoff",
            json={"next_ceiling": "needs_alpha", "at_cap": "propose"},
        )
        assert accept_kickoff.status_code == 200, accept_kickoff.json()
        k = accept_kickoff.json()
        assert k["state"] == "needs_alpha"
        assert k["ceiling"] == "needs_alpha"
        assert k["at_cap"] == "propose"
        assert k["fields"]["kickoff"]["value"] == "kickoff body"
        assert k["fields"]["kickoff"]["proposal"] is None
        assert _event_kinds(client, tid)[base:] == _ACCEPT_EVENTS

        # Drive alpha then beta: propose-with-recap parks on the registry-selected field,
        # then accept advances with the exact next ceiling.
        for field, next_ceiling, next_state in (
            ("alpha", "needs_beta", "needs_beta"),
            ("beta", "none", "done"),
        ):
            # 2-3. Propose the current gating field with a non-empty recap; it PARKS on
            # exactly the field the registry gates for the current state.
            at_cap = "propose" if field == "alpha" else "stop"
            base = len(_event_kinds(client, tid))
            proposed = client.post(
                f"/api/tickets/{tid}/propose",
                json={"body": f"{field} proposal", "recap": f"recap {field}"},
            )
            assert proposed.status_code == 200, proposed.json()
            parked = proposed.json()
            assert parked["fields"][field]["proposal"] is not None
            assert parked["fields"][field]["proposal"]["body"] == f"{field} proposal"
            # The proposal parks: the ceiling is unchanged from before the propose,
            # and no other field carries a proposal.
            assert parked["ceiling"] == k["ceiling"]
            assert _event_kinds(client, tid)[base:] == _PROPOSE_EVENTS

            # 4-5. Accept: exact next state/ceiling, settled value, cleared proposal,
            # and the exact event order.
            base = len(_event_kinds(client, tid))
            accepted = client.post(
                f"/api/tickets/{tid}/accept/{field}",
                json={"next_ceiling": next_ceiling, "at_cap": at_cap},
            )
            assert accepted.status_code == 200, accepted.json()
            a = accepted.json()
            assert a["state"] == next_state
            assert a["at_cap"] == at_cap
            assert a["fields"][field]["value"] == f"{field} proposal"
            assert a["fields"][field]["proposal"] is None
            assert _event_kinds(client, tid)[base:] == _ACCEPT_EVENTS
            k = a

        # 6. Landed at done, ceiling done.
        final = client.get(f"/api/tickets/{tid}").json()
        assert final["state"] == "done"
        assert final["ceiling"] == "done"
        assert final["fields"]["alpha"]["value"] == "alpha proposal"
        assert final["fields"]["beta"]["value"] == "beta proposal"

    # 7. No worker session/turn was ever created — the whole drive is human/API-only.
    conn = connect(str(db_path))
    try:
        row = conn.execute("SELECT chat_session_key FROM tickets WHERE id = ?", (tid,)).fetchone()
        assert row["chat_session_key"] is None
        turns = conn.execute(
            "SELECT count(*) AS n FROM chat_turns WHERE entity_id = ?", (tid,)
        ).fetchone()
        assert turns["n"] == 0
    finally:
        conn.close()


def test_gate_invalid_inputs_return_exact_codes(app_db, probe_installed: None) -> None:
    app, _db_path = app_db
    with TestClient(app) as client:
        made = client.post(
            "/api/tickets",
            json={"title": "Probe errs", "type": "probe", "kickoff_note": "k"},
        )
        tid = made.json()["id"]

        # 8. invalid FIELD (probe has no ghost field).
        ghost = client.post(f"/api/tickets/{tid}/propose/ghost", json={"body": "x"})
        assert ghost.status_code == 400
        assert ghost.json()["error"]["code"] == "validation"
        assert ghost.json()["error"]["message"] == "unknown ticket field"
        assert ghost.json()["error"]["detail"] == {"field": "ghost", "type_id": "probe"}

        # 8. invalid STATE (coding's needs_success is foreign to probe).
        state = client.post(f"/api/tickets/{tid}/state", json={"to": "needs_success"})
        assert state.status_code == 400
        assert state.json()["error"]["code"] == "validation"
        assert state.json()["error"]["message"] == "state outside the linear order"
        assert state.json()["error"]["detail"] == {"state": "needs_success"}

        # 8. invalid CEILING (needs_plan is not in probe's ceiling range).
        scope = client.post(
            f"/api/tickets/{tid}/scope", json={"ceiling": "needs_plan", "at_cap": "stop"}
        )
        assert scope.status_code == 400
        assert scope.json()["error"]["code"] == "scope_invalid"
        assert scope.json()["error"]["message"] == "unknown ceiling"
        assert scope.json()["error"]["detail"] == {"ceiling": "needs_plan"}

        # 8. missing type on create -> the type list.
        missing = client.post("/api/tickets", json={"title": "no type"})
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "validation"
        assert missing.json()["error"]["detail"] == {"type": "", "types": ["coding", "probe"]}

        # 8. unknown type on create -> the type list.
        unknown = client.post("/api/tickets", json={"title": "bad", "type": "nonesuch"})
        assert unknown.status_code == 400
        assert unknown.json()["error"]["code"] == "validation"
        assert unknown.json()["error"]["detail"] == {
            "type": "nonesuch",
            "types": ["coding", "probe"],
        }


def test_gate_error_precedence_not_found_beats_invalid_field(
    app_db, probe_installed: None
) -> None:
    # Resolving the ticket's type before validating the field means a missing ticket
    # is reported as not_found — it beats an invalid-field error.
    app, _db_path = app_db
    with TestClient(app) as client:
        resp = client.post("/api/tickets/t_missing/propose/ghost", json={"body": "x"})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
        assert resp.json()["error"]["detail"] == {"ticket_id": "t_missing"}


def test_gate_external_work_create_and_reconcile_both_types(
    app_db, probe_installed: None
) -> None:
    app, _db_path = app_db
    chief = {"X-Plan-Actor": "chief"}
    with TestClient(app) as client:
        # 9. coding external-work create — the settled prefix (byte-identical to the
        # existing coding external-work behavior: needs_plan settles kickoff/success/approach).
        coding = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "type": "coding",
                "title": "Coding EW",
                "state": "needs_plan",
                "kickoff_note": "kn",
                "success": "s",
                "approach": "a",
            },
            headers=chief,
        )
        assert coding.status_code == 200, coding.json()
        cf = coding.json()["fields"]
        assert coding.json()["state"] == "needs_plan"
        assert {k: cf[k]["value"] for k in ("success", "approach", "plan", "implementation",
                                            "closeout")} == {
            "success": "s", "approach": "a", "plan": None, "implementation": None, "closeout": None
        }

        # 9. probe external-work create at needs_beta — prefix kickoff+alpha, beta empty.
        # Exercises _prefix_count(probe, "needs_beta") == 2 and per-type field marshalling.
        probe_create = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "type": "probe",
                "title": "Probe EW",
                "state": "needs_beta",
                "kickoff_note": "kn",
                "alpha": "alpha settled",
            },
            headers=chief,
        )
        assert probe_create.status_code == 200, probe_create.json()
        pf = probe_create.json()["fields"]
        assert probe_create.json()["state"] == "needs_beta"
        assert pf["kickoff"]["value"] == "kn"
        assert pf["alpha"]["value"] == "alpha settled"
        assert pf["beta"]["value"] is None

        # 9. probe reconcile (prefix reconciliation via supports_prefix_reconciliation):
        # create at needs_alpha, then reconcile forward to needs_beta.
        seed = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "type": "probe",
                "title": "Probe EW reconcile",
                "state": "needs_alpha",
                "kickoff_note": "kn",
            },
            headers=chief,
        )
        assert seed.status_code == 200, seed.json()
        assert seed.json()["state"] == "needs_alpha"
        seed_id = seed.json()["id"]

        reconciled = client.post(
            f"/api/chief/tickets/{seed_id}/reconcile-from-external-work",
            json={"state": "needs_beta", "kickoff_note": "kn", "alpha": "alpha settled"},
            headers=chief,
        )
        assert reconciled.status_code == 200, reconciled.json()
        rf = reconciled.json()["fields"]
        assert reconciled.json()["state"] == "needs_beta"
        assert rf["kickoff"]["value"] == "kn"
        assert rf["alpha"]["value"] == "alpha settled"
        assert rf["beta"]["value"] is None

        # A probe external-work reconcile rejecting an unknown (coding) field key —
        # proves the allowed field-key set is genuinely per-type.
        foreign = client.post(
            f"/api/chief/tickets/{seed_id}/reconcile-from-external-work",
            json={"state": "needs_beta", "kickoff_note": "kn", "success": "coding field"},
            headers=chief,
        )
        assert foreign.status_code == 400
        assert foreign.json()["error"]["code"] == "validation"
        assert foreign.json()["error"]["message"] == "unknown external-work field"
