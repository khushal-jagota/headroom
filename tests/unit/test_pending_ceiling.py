"""An explicit visible-proposal approval shares the canonical accept transaction."""

from __future__ import annotations

from sqlite3 import Connection
from typing import Any, Unpack

import pytest
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data
from planner.tickets.api import _marshal_accept
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
    assert accepted.sprint_item_id == original.sprint_item_id


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


def test_historical_pending_user_owned_answer_still_requires_explicit_accept(
    tmp_db: Connection,
) -> None:
    seed_probe_worker_type(tmp_db)
    install_probe_registry()
    try:
        ticket = data.create_ticket(
            tmp_db,
            title="User review",
            worker_type="probe",
            principal=OWNER_PRINCIPAL,
            brief="Brief",
            stated_ceiling="needs_beta",
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
        )
        worker = Principal(PrincipalKind.ticket, ticket.id)
        ticket = data.file_current_proposal(
            tmp_db, ticket.id, body="Alpha", principal=worker, now=2
        )
        assert ticket.stage == "needs_beta"
        ticket = data.file_current_proposal(
            tmp_db, ticket.id, body="User-owned answer", principal=worker, now=3
        )
        assert ticket.pending_proposal is not None
        # Reproduce a historical proposal parked by the former ownership rule below Done.
        tmp_db.execute("UPDATE tickets SET ceiling='done' WHERE id=?", (ticket.id,))
        ticket = data.read_ticket(tmp_db, ticket.id)
        original = ticket
        for ceiling in ("done", "needs_landing"):
            ticket = _edit(tmp_db, ticket, ceiling=ceiling)
            assert ticket.stage == original.stage
            assert ticket.pending_proposal == original.pending_proposal
            assert ticket.pending_proposal_revision == original.pending_proposal_revision
        # Restoring Done really raises scope past both the saved ceiling and waiting stage.
        with pytest.raises(PlannerError, match="explicit accept"):
            _edit(tmp_db, ticket, ceiling="done")
        accepted = data.accept_proposal(
            tmp_db,
            ticket.id,
            field="beta",
            principal=OWNER_PRINCIPAL,
            now=4,
            next_ceiling="done",
            next_holder=OWNER_PRINCIPAL,
            expected_proposal_revision=ticket.pending_proposal_revision,
        )
        assert accepted.stage == "needs_landing"
        assert accepted.field_values["beta"] == "User-owned answer"
        assert accepted.pending_proposal is None
    finally:
        uninstall_probe_registry()


@pytest.mark.parametrize(
    ("worker_type", "user_stage", "field", "next_stage"),
    [
        ("exploration", "needs_understanding", "understanding", "needs_research_plan"),
        ("probe", "needs_beta", "beta", "needs_landing"),
    ],
)
@pytest.mark.parametrize("at_ceiling", [False, True])
def test_new_user_owned_answer_follows_ceiling_and_releases_step(
    tmp_db: Connection,
    worker_type: str,
    user_stage: str,
    field: str,
    next_stage: str,
    at_ceiling: bool,
) -> None:
    from planner.runtime.worker_step_readiness import is_ready_for_worker_step
    from planner.worker_types.configuration import configured_worker_type_registry

    seed_probe_worker_type(tmp_db)
    install_probe_registry()
    try:
        ticket = data.create_ticket(
            tmp_db,
            title="Collaborative answer",
            worker_type=worker_type,
            principal=OWNER_PRINCIPAL,
            brief="Brief",
            stated_ceiling=user_stage if at_ceiling else next_stage,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
        )
        worker = Principal(PrincipalKind.ticket, ticket.id)
        if worker_type == "probe":
            ticket = data.file_current_proposal(
                tmp_db, ticket.id, body="Alpha", principal=worker, now=2
            )
        assert ticket.stage == user_stage
        definition = configured_worker_type_registry().require(worker_type)
        claimed = data.claim_ticket_for_worker_step(
            tmp_db, ticket.id, readiness_check=is_ready_for_worker_step, now=3
        )
        assert claimed is not None
        assert claimed.worker_step_claim is WorkerStepClaim.out
        assert tmp_db.execute(
            "SELECT stage FROM ticket_paired_stage_openers WHERE ticket_id=?", (ticket.id,)
        ).fetchone()[0] == user_stage
        ticket = data.file_current_proposal(
            tmp_db, ticket.id, body="Shared answer", principal=worker, now=4
        )
        assert ticket.worker_step_claim is WorkerStepClaim.none
        if at_ceiling:
            assert ticket.stage == user_stage
            assert ticket.pending_proposal is not None
            assert ticket.pending_proposal.body == "Shared answer"
            assert field not in ticket.field_values
            assert not is_ready_for_worker_step(tmp_db, ticket, worker_type_definition=definition)
        else:
            assert ticket.stage == next_stage
            assert ticket.field_values[field] == "Shared answer"
            assert ticket.pending_proposal is None
            assert ticket.pending_proposal_revision == 0
            assert tmp_db.execute(
                "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=?", (ticket.id,)
            ).fetchone() is None
            assert is_ready_for_worker_step(tmp_db, ticket, worker_type_definition=definition)
        assert tmp_db.execute("SELECT COUNT(*) FROM manager_wakes").fetchone()[0] == 0
    finally:
        uninstall_probe_registry()


def test_historical_pending_answer_reroute_below_ceiling_still_notifies_chief(
    tmp_db: Connection,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Historical collaborative answer",
        worker_type="exploration",
        principal=OWNER_PRINCIPAL,
        brief="Brief",
        stated_ceiling="needs_understanding",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    ticket = data.file_current_proposal(
        tmp_db, ticket.id, body="Shared understanding",
        principal=Principal(PrincipalKind.ticket, ticket.id), now=2,
    )
    # This persisted state was valid when Stage ownership added a review stop.
    tmp_db.execute("UPDATE tickets SET ceiling='done' WHERE id=?", (ticket.id,))
    original = data.read_ticket(tmp_db, ticket.id)
    rerouted = _edit(tmp_db, original, ceiling_holder=CHIEF_PRINCIPAL)
    assert rerouted.pending_proposal == original.pending_proposal
    assert rerouted.stage == original.stage
    assert rerouted.pending_proposal_revision == original.pending_proposal_revision + 1
    wake = tmp_db.execute(
        "SELECT ticket_id, source_revision, target_kind FROM manager_wakes"
    ).fetchone()
    assert wake is not None
    assert wake["ticket_id"] == ticket.id
    assert wake["source_revision"] == rerouted.pending_proposal_revision
    assert wake["target_kind"] == "chief"


@pytest.mark.parametrize("value", [True, False, 0, -1, "1", 1.5, [], {}])
def test_accept_revision_boundary_requires_a_positive_integer(value: Any) -> None:
    with pytest.raises(PlannerError, match="positive integer"):
        _marshal_accept({"expected_proposal_revision": value})


def test_accept_revision_boundary_preserves_legacy_omission_and_exact_integer() -> None:
    assert _marshal_accept({})["expected_proposal_revision"] is None
    assert (
        _marshal_accept({"expected_proposal_revision": None})["expected_proposal_revision"] is None
    )
    assert _marshal_accept({"expected_proposal_revision": 7})["expected_proposal_revision"] == 7


def test_accept_http_checks_the_visible_revision_before_settling(tmp_db: Connection) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app

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
        response = client.post(f"/api/tickets/{original.id}/accept/success_condition", json=body)
        assert response.status_code == 200, response.text
        accepted = response.json()
        assert accepted["stage"] == "needs_what_changes"
        assert accepted["ceiling"] == "needs_implementation"
        assert accepted["ceiling_holder"] == {"kind": "chief", "id": "chief"}
        assert accepted["field_values"]["success_condition"] == original.pending_proposal.body


def test_visible_accept_continues_through_normal_guarded_claim_without_day(
    tmp_db: Connection,
) -> None:
    from planner.runtime.worker_step_readiness import is_ready_for_worker_step
    from planner.worker_types.configuration import configured_worker_type_registry

    original = _park(tmp_db)
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
    assert tmp_db.execute("SELECT COUNT(*) FROM days").fetchone()[0] == 0
    assert tmp_db.execute("SELECT COUNT(*) FROM day_tickets").fetchone()[0] == 0
    definition = configured_worker_type_registry().require(accepted.worker_type)
    assert is_ready_for_worker_step(tmp_db, accepted, worker_type_definition=definition)
    claimed = data.claim_ticket_for_worker_step(
        tmp_db,
        accepted.id,
        readiness_check=is_ready_for_worker_step,
        now=5,
    )
    assert claimed is not None
    assert claimed.worker_step_claim is WorkerStepClaim.out
    assert data.release_worker_step_claim(
        tmp_db,
        accepted.id,
        expected_claim=claimed.worker_step_claim,
        expected_claim_revision=claimed.worker_step_claim_revision,
        now=6,
    )
    assert data.read_ticket(tmp_db, accepted.id).worker_step_claim is WorkerStepClaim.none
