"""An explicit visible-proposal approval shares the canonical accept transaction."""

from __future__ import annotations

from collections.abc import Iterator
from sqlite3 import Connection
from typing import Unpack

import pytest
from fastapi.testclient import TestClient

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data
from planner.tickets.contracts import TITLE_MAX_CHARS, Ticket, TicketEdit, WorkerStepClaim


def _park(conn: Connection) -> Ticket:
    ticket = data.create_ticket(
        conn,
        title="Visible proposal",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        brief="Brief",
        stated_ceiling="needs_success_condition",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    return data.file_current_proposal(
        conn,
        ticket.id,
        body="Exact visible success condition",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )


def _edit(conn: Connection, ticket: Ticket, **values: Unpack[TicketEdit]) -> Ticket:
    return data.edit_ticket(
        conn,
        ticket.id,
        edit=values,
        principal=OWNER_PRINCIPAL,
        title_max_chars=TITLE_MAX_CHARS,
        now=3,
    )


@pytest.mark.parametrize("ceiling", ["needs_success_condition", "needs_brief"])
def test_pending_same_or_lower_scope_preserves_authored_work(
    tmp_db: Connection,
    ceiling: str,
) -> None:
    original = _park(tmp_db)
    updated = _edit(tmp_db, original, ceiling=ceiling)
    assert updated.ceiling == ceiling
    assert updated.stage == original.stage
    assert updated.pending_proposal == original.pending_proposal
    assert updated.pending_proposal_revision == original.pending_proposal_revision
    assert updated.field_values == original.field_values
    assert updated.ceiling_holder == original.ceiling_holder
    rerouted = _edit(tmp_db, updated, ceiling_holder=CHIEF_PRINCIPAL)
    assert rerouted.ceiling == ceiling
    assert rerouted.pending_proposal == original.pending_proposal
    assert rerouted.stage == original.stage
    assert rerouted.ceiling_holder == CHIEF_PRINCIPAL
    assert rerouted.pending_proposal_revision == original.pending_proposal_revision + 1


def test_generic_scope_patch_cannot_approve_a_pending_proposal(tmp_db: Connection) -> None:
    original = _park(tmp_db)
    with pytest.raises(PlannerError, match="explicit accept"):
        _edit(tmp_db, original, ceiling="done", ceiling_holder=CHIEF_PRINCIPAL)
    assert data.read_ticket(tmp_db, original.id) == original


def test_visible_accept_sets_selected_scope_reviewer_and_releases_claim(tmp_db: Connection) -> None:
    original = _park(tmp_db)
    # An approval must return any extant claim through the canonical writer.
    tmp_db.execute("UPDATE tickets SET worker_step_claim='out' WHERE id=?", (original.id,))
    accepted = data.accept_proposal(
        tmp_db,
        original.id,
        field="success_condition",
        principal=OWNER_PRINCIPAL,
        now=4,
        next_ceiling="needs_implementation",
        next_holder=CHIEF_PRINCIPAL,
        expected_proposal_revision=original.pending_proposal_revision,
    )
    assert accepted.stage == "needs_what_changes"
    assert accepted.ceiling == "needs_implementation"
    assert accepted.ceiling_holder == CHIEF_PRINCIPAL
    assert accepted.pending_proposal is None
    assert accepted.field_values["success_condition"] == "Exact visible success condition"
    assert accepted.worker_step_claim is WorkerStepClaim.none
    assert accepted.manager_id == original.manager_id


@pytest.mark.parametrize("change", ["replacement", "reroute", "field"])
def test_stale_or_wrong_visible_proposal_acceptance_has_no_effects(
    tmp_db: Connection,
    change: str,
) -> None:
    original = _park(tmp_db)
    if change == "replacement":
        data.file_current_proposal(
            tmp_db,
            original.id,
            body="Replacement in the same second",
            principal=Principal(PrincipalKind.ticket, original.id),
            now=2,
        )
    if change == "reroute":
        _edit(tmp_db, original, ceiling_holder=CHIEF_PRINCIPAL)
    before = data.read_ticket(tmp_db, original.id)
    wake_count = tmp_db.execute("SELECT COUNT(*) FROM manager_wakes").fetchone()[0]
    with pytest.raises(PlannerError) as error:
        data.accept_proposal(
            tmp_db,
            original.id,
            field="what_changes" if change == "field" else "success_condition",
            principal=OWNER_PRINCIPAL,
            now=5,
            next_ceiling="done",
            next_holder=OWNER_PRINCIPAL,
            expected_proposal_revision=original.pending_proposal_revision,
        )
    assert error.value.code is ErrorCode.validation
    assert data.read_ticket(tmp_db, original.id) == before
    assert tmp_db.execute("SELECT COUNT(*) FROM manager_wakes").fetchone()[0] == wake_count












def test_accept_http_checks_the_visible_revision_before_settling(tmp_db: Connection) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app
    from planner.managers import data as managers_data

    original = _park(tmp_db)
    assert original.pending_proposal is not None
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(Path(db_path).parent / "logs"),
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(db_path))
    body = {
        "next_ceiling": "needs_implementation",
        "next_holder": {"kind": "chief", "id": "chief"},
        "expected_proposal_revision": original.pending_proposal_revision + 1,
    }
    with TestClient(app) as client:
        response = client.post(f"/api/tickets/{original.id}/accept/success_condition", json=body)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation"
        assert data.read_ticket(tmp_db, original.id) == original
        body["expected_proposal_revision"] = original.pending_proposal_revision
        stranger = managers_data.create_manager(
            tmp_db, title="Unrelated Manager", project_id="project_vylo", clock=build_clock(config)
        )
        tmp_db.execute("UPDATE tickets SET worker_step_claim='out' WHERE id=?", (original.id,))
        tmp_db.commit()
        before = tuple(tmp_db.iterdump())
        refused = client.post(
            f"/api/tickets/{original.id}/accept/success_condition",
            json={**body, "next_holder": {"kind": "manager", "id": stranger.id}},
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "validation"
        assert refused.json()["error"]["message"] == (
            "ceiling holder must have authority to decide this Ticket"
        )
        # The proposal, revision, extant claim, attention, and wake rows stay untouched.
        assert tuple(tmp_db.iterdump()) == before
        response = client.post(f"/api/tickets/{original.id}/accept/success_condition", json=body)
        assert response.status_code == 200, response.text
        accepted = response.json()
        assert accepted["stage"] == "needs_what_changes"
        assert accepted["ceiling"] == "needs_implementation"
        assert accepted["ceiling_holder"] == {"kind": "chief", "id": "chief"}
        assert accepted["field_values"]["success_condition"] == original.pending_proposal.body


@pytest.fixture
def proposal_client(tmp_db: Connection) -> Iterator[TestClient]:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    config = load_config(path=None, env={
        "PLAN_TEST_MODE": "1", "PLAN_DB_PATH": db_path,
        "PLAN_LOGS_DIR": str(Path(db_path).parent / "logs"),
    })
    with TestClient(create_app(config, build_clock(config), lambda: connect(db_path))) as client:
        yield client


@pytest.mark.parametrize("change_time", ["before_request", "after_preflight"])
def test_send_back_http_refuses_changed_revision_without_decision_writes(
    tmp_db: Connection, proposal_client: TestClient, monkeypatch: pytest.MonkeyPatch,
    change_time: str,
) -> None:
    original = _park(tmp_db)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='c_revision' WHERE id=?",
        (original.id,),
    )
    tmp_db.commit()
    before: tuple[str, ...] = ()

    def reroute(conn: Connection) -> None:
        nonlocal before
        _edit(conn, original, ceiling_holder=CHIEF_PRINCIPAL)
        conn.commit()
        before = tuple(conn.iterdump())

    if change_time == "before_request":
        reroute(tmp_db)
    else:
        require_reject = data.require_reject

        def changed_after_preflight(
            conn: Connection, ticket_id: str, *, principal: Principal, has_guidance: bool,
        ) -> Ticket:
            ticket = require_reject(
                conn, ticket_id, principal=principal, has_guidance=has_guidance,
            )
            # A reviewer change leaves the proposal body equal but advances its revision.
            reroute(conn)
            return ticket

        monkeypatch.setattr(data, "require_reject", changed_after_preflight)

    response = proposal_client.post(f"/api/tickets/{original.id}/reject", json={
        "message": "Words for the version I saw",
        "expected_proposal_revision": original.pending_proposal_revision,
    })
    assert response.status_code == 400, response.text
    assert response.json()["error"] == {
        "code": "validation",
        "message": "the visible proposal changed; read it again before sending back",
        "detail": {"ticket_id": original.id},
    }
    # Includes proposal, claim, feedback, attention and wakes: no part of rejection committed.
    assert tuple(tmp_db.iterdump()) == before


def test_send_back_http_validates_revision_and_accepts_matching_or_legacy_request(
    tmp_db: Connection, proposal_client: TestClient,
) -> None:
    original = _park(tmp_db)
    tmp_db.execute("UPDATE tickets SET conversation_id='c_revision' WHERE id=?", (original.id,))
    tmp_db.commit()
    path = f"/api/tickets/{original.id}/reject"
    before = tuple(tmp_db.iterdump())
    for revision in (True, 0, -1, "1", 1.5):
        response = proposal_client.post(path, json={"expected_proposal_revision": revision})
        assert response.status_code == 400, response.text
        assert response.json()["error"]["message"] == (
            "expected_proposal_revision must be a positive integer"
        )
        assert tuple(tmp_db.iterdump()) == before
    response = proposal_client.post(path, json={
        "message": "Revise this version",
        "expected_proposal_revision": original.pending_proposal_revision,
    })
    assert response.status_code == 200, response.text
    assert response.json()["pending_proposal"] is None
    replacement = data.file_current_proposal(
        tmp_db, original.id, body="Replacement",
        principal=Principal(PrincipalKind.ticket, original.id),
        now=5,
    )
    assert replacement.pending_proposal is not None
    # The old web/CLI request remains valid without the optional revision or guidance.
    response = proposal_client.post(path, json={})
    assert response.status_code == 200, response.text
    assert response.json()["pending_proposal"] is None
