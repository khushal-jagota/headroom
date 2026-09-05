"""Acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 for the tickets engine (SPEC §4.4).

Drives everything through planner.tickets.data against a real temp SQLite DB via
the shared conftest fixtures. No mocks. Assertions pin the frozen states, event
payloads, event order, and error codes from the T04 plan.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.contracts import LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.projects import data as projects_data
from planner.runtime import worker_step_readiness
from planner.sprints import data as sprints_data
from planner.tickets import actions, data
from planner.tickets import views as ticket_views
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    StageOwnershipMode,
    TicketEdit,
    TicketStatus,
)
from planner.tickets.logic import fields_codec, machine
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.contracts import WorkerTypeDefinition

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.tickets.contracts import Ticket


@pytest.fixture
def probe_runtime() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


_AUTOMATIC_PLANNING_DAY_ID = "day_2099-01-01"


def _create(conn: Connection, cfg: Config, clock: TestClock, **kw: Any) -> Ticket:
    settle_kickoff = kw.pop("settle_kickoff", True)
    ticket = data.create_ticket(
        conn,
        worker_type="coding",
        title=kw.pop("title", "Test ticket"),
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        **kw,
    )
    if not settle_kickoff:
        return ticket
    ticket = data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    return ticket


def _scope(
    conn: Connection, t: Ticket, ceiling: str, at_cap: AtCap, clock: TestClock
) -> Ticket:
    return data.change_scope(
        conn, t.id, ceiling=ceiling, at_cap=at_cap, actor="human", now=clock.now_unix()
    )


def _claim_ready_worker_step(
    conn: Connection, ticket_id: str, *, now: int
) -> Ticket | None:
    if (
        conn.execute(
            "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
            (_AUTOMATIC_PLANNING_DAY_ID, ticket_id),
        ).fetchone()
        is None
    ):
        days_data.add_day_ticket(conn, _AUTOMATIC_PLANNING_DAY_ID, ticket_id, now)
    return data.claim_ticket_for_worker_step(
        conn,
        ticket_id,
        planning_day_id_resolver=lambda: _AUTOMATIC_PLANNING_DAY_ID,
        readiness_check=worker_step_readiness.is_ready_for_worker_step,
        now=now,
    )


def _ticket_row(conn: Connection, ticket_id: str) -> tuple[object, ...]:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row is not None
    return tuple(row)


def test_ticket_and_field_user_notes_round_trip_with_legacy_field_notes(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, kickoff_note="intake direction")
    assert fields_codec.get_slot(t.fields, "kickoff").value == "intake direction"

    t = data.edit_field_value(
        tmp_db,
        t.id,
        field="kickoff",
        new_body="updated intake direction",
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert (
        fields_codec.get_slot(t.fields, "kickoff").value == "updated intake direction"
    )

    t = data.set_field_user_note(
        tmp_db,
        t.id,
        field="approach",
        user_note="approach guidance",
        actor="agent",
        now=fake_clock.now_unix(),
    )
    assert fields_codec.get_slot(t.fields, "approach").user_note == "approach guidance"

    legacy = json.dumps(
        {
            "kickoff": {
                "value": None,
                "proposal": None,
                "notes": "legacy kickoff guidance",
            },
            "success": {"value": None, "proposal": None, "notes": "legacy guidance"},
            "approach": {"value": None, "proposal": None, "user_note": "new guidance"},
            "plan": {"value": None, "proposal": None, "notes": None},
            "implementation": {"value": None, "proposal": None, "notes": None},
            "closeout": {"value": None, "proposal": None, "notes": None},
        }
    )
    parsed = fields_codec.fields_from_json(legacy)
    assert (
        fields_codec.get_slot(parsed, "kickoff").user_note == "legacy kickoff guidance"
    )
    assert fields_codec.get_slot(parsed, "success").user_note == "legacy guidance"
    assert fields_codec.get_slot(parsed, "approach").user_note == "new guidance"
    assert json.loads(fields_codec.fields_to_json(parsed))["success"] == {
        "value": None,
        "proposal": None,
        "user_note": "legacy guidance",
    }


def test_ordinary_create_parks_ordinary_kickoff_field_proposal(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(
        tmp_db,
        cfg,
        fake_clock,
        title="Draft kickoff title",
        kickoff_note="draft kickoff note",
        settle_kickoff=False,
    )

    assert t.stage == "needs_kickoff"
    assert t.ticket_status is TicketStatus.awaiting_approval
    assert t.title == "Draft kickoff title"
    kickoff_slot = fields_codec.get_slot(t.fields, "kickoff")
    assert kickoff_slot.value is None
    assert kickoff_slot.proposal is not None
    assert kickoff_slot.proposal.body == "draft kickoff note"
    assert set(json.loads(fields_codec.fields_to_json(t.fields))) == {
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    }
    assert (
        machine.has_pending_parked_proposal(
            t, worker_type_definition=CODING_WORKER_TYPE_DEFINITION
        )
        is True
    )


def test_creation_priority_uses_nearest_assessed_anchor_and_exposes_context(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    project = projects_data.create_project(
        tmp_db, name="Assessed project", priority=Priority.P1, now=fake_clock.now_unix()
    )
    item = sprints_data.create_item(
        tmp_db,
        title="Assessed item",
        project_id=project.id,
        priority=Priority.P0,
        clock=fake_clock,
    )

    parented = _create(
        tmp_db,
        cfg,
        fake_clock,
        sprint_item_id=item.id,
        settle_kickoff=False,
    )
    overridden = _create(
        tmp_db,
        cfg,
        fake_clock,
        sprint_item_id=item.id,
        priority=Priority.P2,
        settle_kickoff=False,
    )
    assessed_project = _create(
        tmp_db,
        cfg,
        fake_clock,
        project_id=project.id,
        settle_kickoff=False,
    )
    unassessed_project = _create(
        tmp_db,
        cfg,
        fake_clock,
        project_id="project_other",
        settle_kickoff=False,
    )
    projectless = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)

    assert parented.priority is Priority.P0
    assert overridden.priority is Priority.P2
    assert assessed_project.priority is Priority.P1
    assert unassessed_project.priority is Priority.P3
    assert projectless.priority is Priority.P3

    anchors = parented.resolved_priority_anchors
    assert anchors.sprint_item is not None
    assert (
        anchors.sprint_item.id,
        anchors.sprint_item.title,
        anchors.sprint_item.priority,
    ) == (
        item.id,
        "Assessed item",
        Priority.P0,
    )
    assert anchors.project is not None
    assert (anchors.project.id, anchors.project.name, anchors.project.priority) == (
        project.id,
        "Assessed project",
        Priority.P1,
    )
    assert ticket_views.ticket_json(parented, fake_clock.now_unix())[
        "resolved_priority_anchors"
    ] == {
        "sprint_item": {
            "id": item.id,
            "title": "Assessed item",
            "priority": "P0",
        },
        "project": {
            "id": project.id,
            "name": "Assessed project",
            "priority": "P1",
        },
    }
    assert unassessed_project.resolved_priority_anchors.project is not None
    assert unassessed_project.resolved_priority_anchors.project.priority is None
    assert projectless.resolved_priority_anchors.sprint_item is None
    assert projectless.resolved_priority_anchors.project is None


def test_action_create_uses_worker_default_or_registered_override_before_mutation(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    probe_runtime: None,
) -> None:
    defaulted = actions.create_ticket(
        tmp_db,
        title="Probe default",
        actor="human",
        now=fake_clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="probe",
    )
    overridden = actions.create_ticket(
        tmp_db,
        title="Probe override",
        actor="human",
        now=fake_clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="probe",
        employee_backend="claude",
        employee_launch_model="claude-model",
    )
    tickets_before = tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    with pytest.raises(PlannerError) as raised:
        actions.create_ticket(
            tmp_db,
            title="Probe rejected",
            actor="human",
            now=fake_clock.now_unix(),
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="probe",
            employee_backend="missing-backend",
            employee_launch_model="whatever-it-runs",
        )
    # An override that names no model for the backend it names leaves the Ticket with
    # nothing to run on, so it is refused the same way an unknown backend is.
    with pytest.raises(PlannerError) as unnamed:
        actions.create_ticket(
            tmp_db,
            title="Probe unnamed",
            actor="human",
            now=fake_clock.now_unix(),
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="probe",
            employee_backend="claude",
        )

    assert defaulted.employee_backend == "hermes"
    assert defaulted.employee_launch_model == "probe-model"
    assert overridden.employee_backend == "claude"
    assert overridden.employee_launch_model == "claude-model"
    assert raised.value.code is ErrorCode.validation
    assert unnamed.value.code is ErrorCode.validation
    assert (
        tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == tickets_before
    )


def test_accept_kickoff_field_advances_to_success_and_leaves_title_independent(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(
        tmp_db,
        cfg,
        fake_clock,
        title="Proposed title",
        kickoff_note="proposed note",
        settle_kickoff=False,
    )

    renamed = data.edit_ticket(
        tmp_db,
        t.id,
        edit=TicketEdit(title="Approved title"),
        title_max_chars=TITLE_MAX_CHARS,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert renamed.stage == "needs_kickoff"
    assert fields_codec.get_slot(renamed.fields, "kickoff").proposal is not None

    settled = data.accept_proposal(
        tmp_db,
        t.id,
        field="kickoff",
        actor="human",
        now=fake_clock.now_unix(),
        edited_body="approved note",
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )

    assert settled.stage == "needs_success"
    assert settled.ticket_status is TicketStatus.empty
    assert settled.title == "Approved title"
    assert fields_codec.get_slot(settled.fields, "kickoff").value == "approved note"
    assert fields_codec.get_slot(settled.fields, "kickoff").proposal is None
    assert (
        machine.has_pending_parked_proposal(
            settled, worker_type_definition=CODING_WORKER_TYPE_DEFINITION
        )
        is False
    )


def test_ticket_status_transitions(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    assert t.ticket_status is TicketStatus.empty

    days_data.add_day_ticket(tmp_db, _AUTOMATIC_PLANNING_DAY_ID, t.id, now)
    resolver_calls = 0
    readiness_calls = 0

    def planning_day_id_resolver() -> str:
        nonlocal resolver_calls
        resolver_calls += 1
        assert tmp_db.in_transaction
        return _AUTOMATIC_PLANNING_DAY_ID

    def readiness_check(
        conn: Connection,
        ticket: Ticket,
        *,
        planning_day_id: str,
        worker_type_definition: WorkerTypeDefinition,
    ) -> bool:
        nonlocal readiness_calls
        readiness_calls += 1
        assert conn.in_transaction
        assert ticket == data.read_ticket(conn, t.id)
        assert planning_day_id == _AUTOMATIC_PLANNING_DAY_ID
        assert worker_type_definition.worker_type == ticket.worker_type
        return worker_step_readiness.is_ready_for_worker_step(
            conn,
            ticket,
            planning_day_id=planning_day_id,
            worker_type_definition=worker_type_definition,
        )

    claimed = data.claim_ticket_for_worker_step(
        tmp_db,
        t.id,
        planning_day_id_resolver=planning_day_id_resolver,
        readiness_check=readiness_check,
        now=now,
    )
    assert claimed is not None
    assert claimed.ticket_status is TicketStatus.agent
    assert claimed.ticket_status_changed_at == now
    assert resolver_calls == 1
    assert readiness_calls == 1

    # The claimed Ticket is no longer at `empty`, so the readiness decision inside the
    # transaction is what turns the second claimer away — after running in full.
    skipped = data.claim_ticket_for_worker_step(
        tmp_db,
        t.id,
        planning_day_id_resolver=planning_day_id_resolver,
        readiness_check=readiness_check,
        now=now,
    )
    assert skipped is None
    assert resolver_calls == 2
    assert readiness_calls == 2  # no partial pre-status short circuit

    assert (
        data.release_worker_step_claim(
            tmp_db,
            t.id,
            expected_status=claimed.ticket_status,
            expected_status_changed_at=claimed.ticket_status_changed_at,
            now=now,
        )
        is True
    )
    t = data.read_ticket(tmp_db, t.id)
    assert t.ticket_status is TicketStatus.empty

    claimed_again = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed_again is not None
    t = claimed_again
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="parked", actor="agent", now=now
    )
    assert t.ticket_status is TicketStatus.awaiting_approval

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.ticket_status is TicketStatus.empty

    t = data.take_over_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.user
    skipped = data.claim_ticket_for_worker_step(
        tmp_db,
        t.id,
        planning_day_id_resolver=planning_day_id_resolver,
        readiness_check=readiness_check,
        now=now,
    )
    assert skipped is None
    assert resolver_calls == 3
    assert readiness_calls == 3

    t = data.release_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.empty
    t = data.mark_ticket_errored(tmp_db, t.id, error="boom", now=now)
    assert t.ticket_status is TicketStatus.errored
    assert t.backend_error == "boom"

    t = data.drop_ticket(tmp_db, t.id, actor="human", now=now + 1)
    assert t.ticket_status is TicketStatus.empty
    assert t.backend_error is None
    assert tuple(
        tmp_db.execute(
            "SELECT ticket_status, backend_error FROM tickets WHERE id = ?", (t.id,)
        ).fetchone()
    ) == ("empty", None)


def test_a_claim_release_does_not_fire_once_the_ticket_has_moved_on(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # The status alone is not enough to identify a claim: a Ticket can leave `agent` and
    # come back to it legitimately, and a late release must not erase that second arrival.
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    days_data.add_day_ticket(tmp_db, _AUTOMATIC_PLANNING_DAY_ID, t.id, now)
    claimed = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed is not None

    assert data.release_worker_step_claim(
        tmp_db,
        t.id,
        expected_status=claimed.ticket_status,
        expected_status_changed_at=claimed.ticket_status_changed_at,
        now=now,
    )
    reclaimed = _claim_ready_worker_step(tmp_db, t.id, now=now + 5)
    assert reclaimed is not None
    assert reclaimed.ticket_status is claimed.ticket_status
    assert reclaimed.ticket_status_changed_at != claimed.ticket_status_changed_at

    assert (
        data.release_worker_step_claim(
            tmp_db,
            t.id,
            expected_status=claimed.ticket_status,
            expected_status_changed_at=claimed.ticket_status_changed_at,
            now=now + 6,
        )
        is False
    )
    assert data.read_ticket(tmp_db, t.id).ticket_status is TicketStatus.agent


def test_a_paired_owned_stage_departs_at_paired_and_returns_to_empty(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    data.set_stage_ownership(
        tmp_db,
        t.id,
        stage=t.stage,
        ownership_mode=StageOwnershipMode.paired,
        now=now,
    )
    days_data.add_day_ticket(tmp_db, _AUTOMATIC_PLANNING_DAY_ID, t.id, now)

    claimed = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed is not None
    assert claimed.ticket_status is TicketStatus.paired

    assert data.release_worker_step_claim(
        tmp_db,
        t.id,
        expected_status=claimed.ticket_status,
        expected_status_changed_at=claimed.ticket_status_changed_at,
        now=now + 1,
    )
    assert data.read_ticket(tmp_db, t.id).ticket_status is TicketStatus.empty


def _park_paired(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    now: int,
) -> Ticket:
    t = _create(tmp_db, cfg, fake_clock)
    claimed = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed is not None
    t = claimed
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = ? WHERE id = ?",
        (f"conv-{t.id}", t.id),
    )
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="parked", actor="agent", now=now
    )
    assert t.ticket_status is TicketStatus.awaiting_approval
    t = data.enter_paired_on_human_reply(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.paired
    return t


def test_enter_paired_on_human_reply_flips_only_from_awaiting_approval(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    assert t.ticket_status is TicketStatus.empty
    # No-op from empty.
    t = data.enter_paired_on_human_reply(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.empty
    # No-op from agent.
    claimed = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed is not None
    t = claimed
    assert t.ticket_status is TicketStatus.agent
    t = data.enter_paired_on_human_reply(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.agent
    # Flips from awaiting_approval.
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="parked", actor="agent", now=now
    )
    assert t.ticket_status is TicketStatus.awaiting_approval
    t = data.enter_paired_on_human_reply(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.paired


def test_paired_exit_send_back_reopens_and_clears_proposal(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _park_paired(tmp_db, cfg, fake_clock, now)
    t = data.return_for_revision(
        tmp_db, t.id, message="please revise", actor="human", now=now
    )
    assert t.ticket_status is TicketStatus.agent
    fields = json.loads(
        tmp_db.execute("SELECT fields FROM tickets WHERE id = ?", (t.id,)).fetchone()[
            "fields"
        ]
    )
    assert fields["success"]["proposal"] is None


@pytest.mark.parametrize("implementation_owner", [StageOwnershipMode.user, None])
def test_direct_plan_accept_derives_implementation_ownership_status(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    implementation_owner: StageOwnershipMode | None,
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock)
    data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_implementation",
        ownership_mode=implementation_owner,
        now=now,
    )
    _scope(tmp_db, ticket, "needs_plan", AtCap.propose, fake_clock)
    for field in ("success", "approach", "plan"):
        ticket = data.file_proposal(
            tmp_db,
            ticket.id,
            field=field,
            body=f"{field} body",
            actor="agent",
            now=now,
        )
    assert ticket.stage == "needs_plan"
    assert ticket.ticket_status is TicketStatus.awaiting_approval

    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="plan",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )

    assert ticket.stage == "needs_implementation"
    expected = TicketStatus.user if implementation_owner else TicketStatus.empty
    assert ticket.ticket_status is expected


@pytest.mark.parametrize("implementation_owner", [StageOwnershipMode.user, None])
def test_auto_accepted_plan_derives_implementation_ownership_status(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    implementation_owner: StageOwnershipMode | None,
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock)
    data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_implementation",
        ownership_mode=implementation_owner,
        now=now,
    )
    _scope(tmp_db, ticket, "needs_implementation", AtCap.propose, fake_clock)
    for field in ("success", "approach"):
        ticket = data.file_proposal(
            tmp_db,
            ticket.id,
            field=field,
            body=f"{field} body",
            actor="agent",
            now=now,
        )
    started = _claim_ready_worker_step(tmp_db, ticket.id, now=now)
    assert started is not None

    ticket = data.file_proposal(
        tmp_db,
        ticket.id,
        field="plan",
        body="plan body",
        actor="agent",
        now=now,
    )
    assert ticket.stage == "needs_implementation"
    before_settlement = (
        TicketStatus.user
        if implementation_owner is StageOwnershipMode.user
        else TicketStatus.empty
    )
    assert ticket.ticket_status is before_settlement


def test_a02_gating_chain_one_state_per_accept(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_closeout", AtCap.propose, fake_clock)

    t = data.file_proposal(
        tmp_db, t.id, field="success", body="success body", actor="agent", now=now
    )
    assert t.stage == "needs_approach"
    assert fields_codec.get_slot(t.fields, "success").value == "success body"
    assert fields_codec.get_slot(t.fields, "success").proposal is None

    t = data.file_proposal(
        tmp_db, t.id, field="approach", body="approach body", actor="agent", now=now
    )
    assert t.stage == "needs_plan"
    assert fields_codec.get_slot(t.fields, "approach").value == "approach body"

    t = data.file_proposal(
        tmp_db, t.id, field="plan", body="plan body", actor="agent", now=now
    )
    assert t.stage == "needs_implementation"
    assert fields_codec.get_slot(t.fields, "plan").value == "plan body"

    t = data.file_proposal(
        tmp_db,
        t.id,
        field="implementation",
        body="implementation body",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_closeout"
    assert (
        fields_codec.get_slot(t.fields, "implementation").value == "implementation body"
    )
    assert all(
        fields_codec.get_slot(t.fields, field).proposal is None
        for field in ("success", "approach", "plan", "implementation")
    )


def test_a03_ceiling_auto_accept_until_cap_then_pending(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", AtCap.propose, fake_clock)

    t = data.file_proposal(
        tmp_db, t.id, field="success", body="s", actor="agent", now=now
    )
    assert t.stage == "needs_approach"
    t = data.file_proposal(
        tmp_db, t.id, field="approach", body="a", actor="agent", now=now
    )
    assert t.stage == "needs_plan"

    t = data.file_proposal(
        tmp_db, t.id, field="plan", body="plan body", actor="agent", now=now
    )
    assert t.stage == "needs_plan"
    plan_slot = fields_codec.get_slot(t.fields, "plan")
    assert plan_slot.value is None
    assert plan_slot.proposal is not None
    assert plan_slot.proposal.body == "plan body"
    assert plan_slot.proposal.proposed_by == "agent"
    assert (
        machine.has_pending_gating_proposal(
            t.stage,
            t.fields,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
        is True
    )

    assert t.ticket_status is TicketStatus.awaiting_approval


def test_a05_one_pending_proposal_per_field_supersede(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    t = data.file_proposal(
        tmp_db, t.id, field="success", body="first body", actor="agent", now=now
    )
    first_success = fields_codec.get_slot(t.fields, "success")
    assert first_success.proposal is not None
    assert first_success.proposal.body == "first body"

    t = data.file_proposal(
        tmp_db, t.id, field="success", body="second body", actor="agent", now=now
    )
    second_success = fields_codec.get_slot(t.fields, "success")
    assert second_success.proposal is not None
    assert second_success.proposal.body == "second body"

    assert t.stage == "needs_success"


def test_a06_edit_accept_stores_edited_text(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="draft body", actor="agent", now=now
    )

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        actor="human",
        now=now,
        edited_body="edited body exactly",
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert fields_codec.get_slot(t.fields, "success").value == "edited body exactly"
    assert fields_codec.get_slot(t.fields, "success").proposal is None

    assert t.stage == "needs_approach"
    assert t.ceiling == "needs_approach"
    assert t.at_cap is AtCap.propose


def test_a07_closeout_routing(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()

    # Ceiling stops exactly at needs_closeout: implementation auto-accepts up to
    # needs_closeout, and closeout then routes to done through the ordinary
    # accept machinery (no special-cased manual review step).
    t1 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t1, "needs_closeout", AtCap.propose, fake_clock)
    for f, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
    ]:
        t1 = data.file_proposal(
            tmp_db, t1.id, field=f, body=body, actor="agent", now=now
        )
    assert t1.stage == "needs_closeout"
    t1 = data.file_proposal(
        tmp_db, t1.id, field="closeout", body="c", actor="agent", now=now
    )
    assert t1.stage == "needs_closeout"
    assert fields_codec.get_slot(t1.fields, "closeout").proposal is not None
    assert fields_codec.get_slot(t1.fields, "closeout").value is None

    t1 = data.accept_proposal(
        tmp_db,
        t1.id,
        field="closeout",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t1.stage == "done"
    assert fields_codec.get_slot(t1.fields, "closeout").value == "c"
    assert t1.ceiling == "done"
    assert t1.at_cap is AtCap.propose

    # Ceiling at done from the start: every field, including closeout, auto-accepts
    # straight through to done.
    t2 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t2, "done", AtCap.propose, fake_clock)
    for f, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("closeout", "c"),
    ]:
        t2 = data.file_proposal(
            tmp_db, t2.id, field=f, body=body, actor="agent", now=now
        )
    assert t2.stage == "done"
    assert all(
        fields_codec.get_slot(t2.fields, field).value == body
        for field, body in [
            ("success", "s"),
            ("approach", "a"),
            ("plan", "p"),
            ("implementation", "i"),
            ("closeout", "c"),
        ]
    )

    # Ceiling below needs_closeout: the closeout proposal stays pending until the
    # ceiling is raised and it is explicitly accepted.
    t3 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t3, "needs_implementation", AtCap.propose, fake_clock)
    for f, body in [("success", "s"), ("approach", "a"), ("plan", "p")]:
        t3 = data.file_proposal(
            tmp_db, t3.id, field=f, body=body, actor="agent", now=now
        )
    assert t3.stage == "needs_implementation"
    t3 = data.file_proposal(
        tmp_db, t3.id, field="implementation", body="i", actor="agent", now=now
    )
    assert t3.stage == "needs_implementation"
    assert fields_codec.get_slot(t3.fields, "implementation").proposal is not None

    _scope(tmp_db, t3, "done", AtCap.propose, fake_clock)
    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field="implementation",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )
    assert t3.stage == "needs_closeout"


def test_a08_recap_rules(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    # Recap is never blocked: writable from the first worker stage.
    assert t.stage == "needs_success"
    t = data.write_recap(tmp_db, t.id, body="first recap", actor="agent", now=now)
    assert t.recap == "first recap"
    assert t.stage == "needs_success"

    _scope(tmp_db, t, "needs_approach", AtCap.propose, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="s", actor="agent", now=now
    )
    assert t.stage == "needs_approach"

    t = data.write_recap(tmp_db, t.id, body="second recap", actor="agent", now=now)
    assert t.recap == "second recap"
    assert t.stage == "needs_approach"

    # And still writable on a terminal ticket.
    t = data.drop_ticket(tmp_db, t.id, actor="human", now=now)
    assert t.stage == "dropped"
    t = data.write_recap(tmp_db, t.id, body="post-drop recap", actor="agent", now=now)
    assert t.recap == "post-drop recap"


def test_a13_sprint_placement_is_derived_from_item_membership(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_test', 'Test sprint', '2026-07-01', '2026-07-12', ?, ?)",
        (now, now),
    )

    tmp_db.execute(
        "INSERT INTO sprint_items (id, title, project_id, sprint_id, created_at, updated_at) "
        "VALUES ('si_test', 'Parent item', 'project_vylo', 'sp_test', ?, ?)",
        (now, now),
    )
    parented = _create(tmp_db, cfg, fake_clock, sprint_item_id="si_test")
    backlog = _create(tmp_db, cfg, fake_clock)
    assert data.get_effective_sprint_id(tmp_db, parented.id) == "sp_test"
    assert data.get_effective_sprint_id(tmp_db, backlog.id) is None


def test_a36_onward_scope(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()

    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="body", actor="agent", now=now
    )
    assert fields_codec.get_slot(t.fields, "success").proposal is not None
    row_before = _ticket_row(tmp_db, t.id)

    with pytest.raises(PlannerError) as e_missing_ceiling:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            actor="human",
            now=now,
            next_ceiling=None,
            at_cap=AtCap.stop,
        )
    assert e_missing_ceiling.value.code is ErrorCode.scope_missing
    with pytest.raises(PlannerError) as e_missing_at_cap:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            actor="human",
            now=now,
            next_ceiling=NO_FURTHER,
            at_cap=None,
        )
    assert e_missing_at_cap.value.code is ErrorCode.scope_missing

    t = data.read_ticket(tmp_db, t.id)
    assert t.stage == "needs_success"
    success_slot = fields_codec.get_slot(t.fields, "success")
    assert success_slot.value is None
    assert success_slot.proposal is not None
    assert success_slot.proposal.body == "body"
    assert t.ceiling == "needs_success"
    assert t.at_cap is AtCap.propose
    assert _ticket_row(tmp_db, t.id) == row_before

    with pytest.raises(PlannerError) as e_before:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            actor="human",
            now=now,
            next_ceiling="needs_success",
            at_cap=AtCap.propose,
        )
    assert e_before.value.code is ErrorCode.scope_invalid
    with pytest.raises(PlannerError) as e_dropped:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            actor="human",
            now=now,
            next_ceiling="dropped",
            at_cap=AtCap.propose,
        )
    assert e_dropped.value.code is ErrorCode.scope_invalid
    assert data.read_ticket(tmp_db, t.id).stage == "needs_success"
    assert _ticket_row(tmp_db, t.id) == row_before

    with pytest.raises(PlannerError) as e_agent:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            actor="agent",
            now=now,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    assert e_agent.value.code is ErrorCode.agent_forbidden
    assert _ticket_row(tmp_db, t.id) == row_before

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )
    assert t.stage == "needs_approach"
    assert t.ceiling == "needs_approach"
    assert t.at_cap is AtCap.stop
    with pytest.raises(PlannerError) as e_rest:
        data.file_proposal(
            tmp_db, t.id, field="approach", body="x", actor="agent", now=now
        )
    assert e_rest.value.code is ErrorCode.at_cap_stop

    t2 = _create(tmp_db, cfg, fake_clock)
    t2 = data.file_proposal(
        tmp_db, t2.id, field="success", body="body", actor="agent", now=now
    )
    t2 = data.accept_proposal(
        tmp_db,
        t2.id,
        field="success",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t2.stage == "needs_approach"
    t2 = data.file_proposal(
        tmp_db, t2.id, field="approach", body="draft", actor="agent", now=now
    )
    assert fields_codec.get_slot(t2.fields, "approach").proposal is not None
    assert t2.stage == "needs_approach"

    t3 = _create(tmp_db, cfg, fake_clock)
    t3 = data.file_proposal(
        tmp_db, t3.id, field="success", body="body", actor="agent", now=now
    )
    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field="success",
        actor="human",
        now=now,
        next_ceiling="needs_plan",
        at_cap=AtCap.propose,
    )
    assert t3.ceiling == "needs_plan"
    t3 = data.file_proposal(
        tmp_db, t3.id, field="approach", body="a", actor="agent", now=now
    )
    assert t3.stage == "needs_plan"
    assert t3.ceiling == "needs_plan"
    assert t3.at_cap is AtCap.propose

    t4 = _create(tmp_db, cfg, fake_clock)
    t4 = _scope(tmp_db, t4, "done", AtCap.propose, fake_clock)
    ceiling_before = t4.ceiling
    at_cap_before = t4.at_cap
    for f, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("closeout", "c"),
    ]:
        t4 = data.file_proposal(
            tmp_db, t4.id, field=f, body=body, actor="agent", now=now
        )
    assert t4.stage == "done"
    assert t4.ceiling == ceiling_before
    assert t4.at_cap is at_cap_before


def test_read_ticket_by_conversation_id(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    """A worker resolves its own ticket from its live session key; unknown key -> not_found."""
    t = _create(tmp_db, cfg, fake_clock)
    tmp_db.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES ('sess_abc', 'codex', 'model', '/work', 'full', 1)"
    )
    tmp_db.execute(
        "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
        ("sess_abc", t.id),
    )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = ? WHERE id = ?", ("sess_abc", t.id)
    )
    assert data.read_ticket_by_conversation_id(tmp_db, "sess_abc").id == t.id
    with pytest.raises(PlannerError) as exc:
        data.read_ticket_by_conversation_id(tmp_db, "no_such_session")
    assert exc.value.code is ErrorCode.not_found


# --- blocked: empty's stand-in while a live blocker exists ----------------------


def _block(conn: Connection, *, blocker_id: str, target_id: str, now: int) -> None:
    """Block an entity the way the API does, so a Ticket target settles to `blocked`."""
    actions.add_link(conn, blocker_id, target_id, LinkKind.blocks, now=now)


def _links_from(conn: Connection, ticket_id: str) -> list[tuple[str, str]]:
    return [
        (str(row["to_id"]), str(row["kind"]))
        for row in conn.execute(
            "SELECT to_id, kind FROM links WHERE from_id = ? ORDER BY to_id",
            (ticket_id,),
        ).fetchall()
    ]


def test_link_admission_runs_inside_each_write_transaction(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    admission_calls: list[str] = []

    def admit_add() -> None:
        assert tmp_db.in_transaction
        admission_calls.append("add")

    def admit_remove() -> None:
        assert tmp_db.in_transaction
        admission_calls.append("remove")

    actions.add_link(
        tmp_db,
        blocker.id,
        target.id,
        LinkKind.blocks,
        now=now,
        admit=admit_add,
    )
    assert not tmp_db.in_transaction
    actions.remove_link(
        tmp_db,
        blocker.id,
        target.id,
        LinkKind.blocks,
        now=now,
        admit=admit_remove,
    )

    assert not tmp_db.in_transaction
    assert admission_calls == ["add", "remove"]


def test_a_second_live_blocker_holds_the_target_blocked_until_both_clear(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    first = _create(tmp_db, cfg, fake_clock, title="First blocker")
    second = _create(tmp_db, cfg, fake_clock, title="Second blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")

    _block(tmp_db, blocker_id=first.id, target_id=target.id, now=now)
    _block(tmp_db, blocker_id=second.id, target_id=target.id, now=now)
    # The second acquisition finds the target already blocked and changes nothing.
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked

    data.set_stage(tmp_db, first.id, new_stage="done", actor="human", now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked

    data.set_stage(tmp_db, second.id, new_stage="done", actor="human", now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty


@pytest.mark.parametrize("completion", ["done", "dropped"])
def test_completing_a_blocker_releases_its_links_and_frees_the_target(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock, completion: str
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    assert _links_from(tmp_db, blocker.id) == [(target.id, "blocks")]

    if completion == "done":
        data.set_stage(tmp_db, blocker.id, new_stage="done", actor="human", now=now)
    else:
        data.drop_ticket(tmp_db, blocker.id, actor="human", now=now)

    assert _links_from(tmp_db, blocker.id) == []
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty


def test_reopening_a_done_blocker_blocks_its_target_again(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    data.set_stage(tmp_db, blocker.id, new_stage="done", actor="human", now=now)

    # Linking from a done Ticket stays permitted and blocks nothing: the source is dead.
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty

    data.set_stage(
        tmp_db, blocker.id, new_stage="needs_success", actor="human", now=now
    )
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked


def test_a_failed_completion_rolls_back_its_link_release_and_settlements(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The stage change, the link deletion, and every target settlement are one
    # transaction: when any part of the completion fails, none of it lands.
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    stage_before = data.read_ticket(tmp_db, blocker.id).stage
    target_row_before = _ticket_row(tmp_db, target.id)

    def failing_settle(conn: Connection, target_id: str, now: int) -> None:
        raise RuntimeError("settlement failed mid-completion")

    monkeypatch.setattr(data, "settle_blocked_standin_for_link_target", failing_settle)
    with pytest.raises(RuntimeError, match="mid-completion"):
        data.drop_ticket(tmp_db, blocker.id, actor="human", now=now)

    assert data.read_ticket(tmp_db, blocker.id).stage == stage_before
    assert _links_from(tmp_db, blocker.id) == [(target.id, "blocks")]
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked
    assert _ticket_row(tmp_db, target.id) == target_row_before


