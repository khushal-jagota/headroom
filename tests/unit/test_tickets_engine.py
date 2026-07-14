"""Acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 for the tickets engine (SPEC §4.4).

Drives everything through planner.tickets.data against a real temp SQLite DB via
the shared conftest fixtures. No mocks. Assertions pin the frozen states, event
payloads, event order, and error codes from the T04 plan.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.days import data as days_data
from planner.runtime import automatic_employee_step_eligibility
from planner.tickets import actions, data
from planner.tickets import views as ticket_views
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    Implementer,
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
    from planner.core.contracts import EventRow
    from planner.tickets.contracts import Ticket


class _RecordingEligibilityWake:
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


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
    conn.execute(
        "DELETE FROM events WHERE entity_id = ? AND kind IN ("
        "'proposal_filed', 'proposal_accepted', 'stage_changed', 'scope_changed', "
        "'ticket_status_changed')",
        (ticket.id,),
    )
    return ticket


def _scope(conn: Connection, t: Ticket, ceiling: str, at_cap: AtCap, clock: TestClock) -> Ticket:
    return data.change_scope(
        conn, t.id, ceiling=ceiling, at_cap=at_cap, actor="human", now=clock.now_unix()
    )


def _claim_eligible_automatic_step(conn: Connection, ticket_id: str, *, now: int) -> Ticket | None:
    if (
        conn.execute(
            "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
            (_AUTOMATIC_PLANNING_DAY_ID, ticket_id),
        ).fetchone()
        is None
    ):
        days_data.add_day_ticket(conn, _AUTOMATIC_PLANNING_DAY_ID, ticket_id, now)
    return data.claim_automatic_employee_step(
        conn,
        ticket_id,
        planning_day_id_resolver=lambda: _AUTOMATIC_PLANNING_DAY_ID,
        eligibility_check=(
            automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
        ),
        now=now,
    )


def _events(
    conn: Connection, cfg: Config, ticket_id: str, kind: EventKind | None = None
) -> list[EventRow]:
    rows = read_events_since(conn, 0, cfg.events_read_limit)
    return [e for e in rows if e.entity_id == ticket_id and (kind is None or e.kind == kind.value)]


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
    assert fields_codec.get_slot(t.fields, "kickoff").value == "updated intake direction"

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
            "kickoff": {"value": None, "proposal": None, "notes": "legacy kickoff guidance"},
            "success": {"value": None, "proposal": None, "notes": "legacy guidance"},
            "approach": {"value": None, "proposal": None, "user_note": "new guidance"},
            "plan": {"value": None, "proposal": None, "notes": None},
            "implementation": {"value": None, "proposal": None, "notes": None},
            "closeout": {"value": None, "proposal": None, "notes": None},
        }
    )
    parsed = fields_codec.fields_from_json(legacy)
    assert fields_codec.get_slot(parsed, "kickoff").user_note == "legacy kickoff guidance"
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
    assert fields_codec.get_slot(t.fields, "kickoff").value is None
    assert fields_codec.get_slot(t.fields, "kickoff").proposal is not None
    assert fields_codec.get_slot(t.fields, "kickoff").proposal.body == "draft kickoff note"
    assert set(json.loads(fields_codec.fields_to_json(t.fields))) == {
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    }
    proposal_events = _events(tmp_db, cfg, t.id, EventKind.proposal_filed)
    assert proposal_events[-1].payload == {
        "field": "kickoff",
        "body": "draft kickoff note",
        "proposed_by": "human",
    }
    assert (
        machine.has_pending_parked_proposal(t, worker_type_definition=CODING_WORKER_TYPE_DEFINITION)
        is True
    )


def test_review_queue_exposes_kickoff_as_ordinary_field_approval(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)
    day_id = "day_2026-07-04"
    days_data.add_day_ticket(tmp_db, day_id, t.id, fake_clock.now_unix())

    queues = ticket_views.queues_view(
        tmp_db,
        now=fake_clock.now_unix(),
        today_iso="2026-07-04",
        item_approval_rows=[],
        item_overdue_rows=[],
        day_id=day_id,
    )

    assert queues["approvals"] == [
        {
            "entity_id": t.id,
            "entity_type": "ticket",
            "kind": "kickoff",
            "title": t.title,
            "waiting_since": fields_codec.get_slot(t.fields, "kickoff").proposal.created_at,
        }
    ]


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
    accepted = _events(tmp_db, cfg, t.id, EventKind.proposal_accepted)
    assert accepted[-1].payload["field"] == "kickoff"
    assert accepted[-1].payload["resolved_by"] == "direct"
    assert accepted[-1].payload["edited"] is True


def test_accept_kickoff_field_action_wakes_and_leaves_ticket_eligible(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)
    eligibility_wake = _RecordingEligibilityWake()

    settled = actions.accept_proposal(
        tmp_db,
        t.id,
        field="kickoff",
        actor="human",
        now=fake_clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        automatic_employee_step_eligibility_wake=eligibility_wake,
    )

    assert eligibility_wake.wakes == 1
    assert settled.stage == "needs_success"
    days_data.add_day_ticket(tmp_db, _AUTOMATIC_PLANNING_DAY_ID, settled.id, fake_clock.now_unix())
    assert (
        automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step(
            tmp_db,
            settled,
            planning_day_id=_AUTOMATIC_PLANNING_DAY_ID,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
        is True
    )


def test_kickoff_pending_allows_title_edit_but_guards_takeover_and_release(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)

    edited = data.edit_ticket(
        tmp_db,
        t.id,
        edit=TicketEdit(title="edited"),
        title_max_chars=TITLE_MAX_CHARS,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert edited.title == "edited"
    assert fields_codec.get_slot(edited.fields, "kickoff").proposal is not None
    with pytest.raises(PlannerError, match="before takeover"):
        data.take_over_ticket(tmp_db, t.id, now=fake_clock.now_unix())
    with pytest.raises(PlannerError, match="before release"):
        data.release_ticket(tmp_db, t.id, now=fake_clock.now_unix())


def test_direct_state_changes_cannot_bypass_or_reenter_kickoff(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    pending = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)
    with pytest.raises(PlannerError, match="only through kickoff approval"):
        data.set_stage(
            tmp_db,
            pending.id,
            new_stage="needs_success",
            actor="human",
            now=fake_clock.now_unix(),
        )

    settled = data.accept_proposal(
        tmp_db,
        pending.id,
        field="kickoff",
        actor="human",
        now=fake_clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert settled.stage == "needs_success"
    with pytest.raises(PlannerError, match="only through kickoff approval"):
        data.set_stage(
            tmp_db,
            pending.id,
            new_stage="needs_kickoff",
            actor="human",
            now=fake_clock.now_unix(),
        )


def test_ticket_status_transitions(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    assert t.ticket_status is TicketStatus.empty

    days_data.add_day_ticket(tmp_db, _AUTOMATIC_PLANNING_DAY_ID, t.id, now)
    resolver_calls = 0
    eligibility_calls = 0

    def planning_day_id_resolver() -> str:
        nonlocal resolver_calls
        resolver_calls += 1
        assert tmp_db.in_transaction
        return _AUTOMATIC_PLANNING_DAY_ID

    def eligibility_check(
        conn: Connection,
        ticket: Ticket,
        *,
        planning_day_id: str,
        worker_type_definition: WorkerTypeDefinition,
    ) -> bool:
        nonlocal eligibility_calls
        eligibility_calls += 1
        assert conn.in_transaction
        assert ticket == data.read_ticket(conn, t.id)
        assert planning_day_id == _AUTOMATIC_PLANNING_DAY_ID
        assert worker_type_definition.worker_type == ticket.worker_type
        return automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step(
            conn,
            ticket,
            planning_day_id=planning_day_id,
            worker_type_definition=worker_type_definition,
        )

    started = data.claim_automatic_employee_step(
        tmp_db,
        t.id,
        planning_day_id_resolver=planning_day_id_resolver,
        eligibility_check=eligibility_check,
        now=now,
    )
    assert started is not None
    assert started.ticket_status is TicketStatus.agent_running_step
    assert resolver_calls == 1
    assert eligibility_calls == 1

    skipped = data.claim_automatic_employee_step(
        tmp_db,
        t.id,
        planning_day_id_resolver=planning_day_id_resolver,
        eligibility_check=eligibility_check,
        now=now,
    )
    assert skipped is None
    assert resolver_calls == 2
    assert eligibility_calls == 2  # no partial pre-status short circuit

    t = data.finish_run_if_still_running_step(tmp_db, t.id, session_key="sess-1", now=now)
    assert t.ticket_status is TicketStatus.empty
    assert t.chat_session_key == "sess-1"

    t = _claim_eligible_automatic_step(tmp_db, t.id, now=now)
    assert t is not None
    t = data.file_proposal(tmp_db, t.id, field="success", body="parked", actor="agent", now=now)
    assert t.ticket_status is TicketStatus.awaiting_approval
    t = data.finish_run_if_still_running_step(tmp_db, t.id, session_key="sess-2", now=now)
    assert t.ticket_status is TicketStatus.awaiting_approval
    assert t.chat_session_key == "sess-2"

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
    assert t.ticket_status is TicketStatus.user_takeover
    skipped = data.claim_automatic_employee_step(
        tmp_db,
        t.id,
        planning_day_id_resolver=planning_day_id_resolver,
        eligibility_check=eligibility_check,
        now=now,
    )
    assert skipped is None
    assert resolver_calls == 3
    assert eligibility_calls == 3

    t = data.release_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.empty
    t = data.mark_run_errored(tmp_db, t.id, error="boom", session_key="sess-3", now=now)
    assert t.ticket_status is TicketStatus.errored
    assert t.chat_session_key == "sess-3"

    status_events = _events(tmp_db, cfg, t.id, EventKind.ticket_status_changed)
    assert all("worker" not in e.payload for e in status_events)
    assert status_events[-1].payload == {"ticket_status": "errored", "error": "boom"}


def test_claim_running_step_chat_session_key_logs_lookup_event(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _claim_eligible_automatic_step(tmp_db, t.id, now=now)

    updated = data.claim_running_step_chat_session_key(
        tmp_db, t.id, session_key="sess-early", now=now
    )

    assert updated.chat_session_key == "sess-early"
    assert data.read_ticket_by_session_key(tmp_db, "sess-early").id == t.id
    events = _events(tmp_db, cfg, t.id, EventKind.chat_session_created)
    assert [event.payload for event in events] == [{"session_key": "sess-early"}]


def test_claim_running_step_chat_session_key_does_not_overwrite_non_running_ticket(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    updated = data.claim_running_step_chat_session_key(
        tmp_db, t.id, session_key="sess-early", now=now
    )

    assert updated.chat_session_key is None
    events = _events(tmp_db, cfg, t.id, EventKind.chat_session_created)
    assert events == []


def test_mark_run_errored_if_still_running_step_preserves_lost_ownership(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _claim_eligible_automatic_step(tmp_db, t.id, now=now)
    t = data.mark_run_errored_if_still_running_step(
        tmp_db, t.id, error="boom", session_key="sess-error", now=now
    )
    assert t.ticket_status is TicketStatus.errored
    assert t.chat_session_key == "sess-error"

    t = data.release_ticket(tmp_db, t.id, now=now)
    t = _claim_eligible_automatic_step(tmp_db, t.id, now=now)
    assert t is not None
    t = data.take_over_ticket(tmp_db, t.id, now=now)
    t = data.mark_run_errored_if_still_running_step(
        tmp_db, t.id, error="late boom", session_key="sess-late", now=now
    )

    assert t.ticket_status is TicketStatus.user_takeover
    assert t.chat_session_key == "sess-error"
    status_events = _events(tmp_db, cfg, t.id, EventKind.ticket_status_changed)
    assert status_events[-1].payload == {"ticket_status": "user_takeover"}


@pytest.mark.parametrize("implementer", [*Implementer, None])
def test_direct_plan_accept_routes_only_khushal_to_user_takeover(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    implementer: Implementer | None,
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock, implementer=implementer)
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
    expected = (
        TicketStatus.user_takeover if implementer is Implementer.khushal else TicketStatus.empty
    )
    assert ticket.ticket_status is expected


@pytest.mark.parametrize("implementer", [*Implementer, None])
def test_auto_accepted_plan_routes_only_khushal_to_takeover_that_survives_settlement(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    implementer: Implementer | None,
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock, implementer=implementer)
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
    started = _claim_eligible_automatic_step(tmp_db, ticket.id, now=now)
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
        TicketStatus.user_takeover
        if implementer is Implementer.khushal
        else TicketStatus.agent_running_step
    )
    assert ticket.ticket_status is before_settlement

    settled = data.finish_run_if_still_running_step(tmp_db, ticket.id, now=now)
    expected = (
        TicketStatus.user_takeover if implementer is Implementer.khushal else TicketStatus.empty
    )
    assert settled.ticket_status is expected


def test_current_worker_plan_proposal_routes_khushal_to_takeover_that_survives_settlement(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock, implementer=Implementer.khushal)
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
    started = _claim_eligible_automatic_step(tmp_db, ticket.id, now=now)
    assert started is not None

    ticket = data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="plan body",
        recap="Plan ready for implementation.",
        actor="agent",
        now=now,
    )

    assert ticket.stage == "needs_implementation"
    assert ticket.ticket_status is TicketStatus.user_takeover
    settled = data.finish_run_if_still_running_step(tmp_db, ticket.id, now=now)
    assert settled.ticket_status is TicketStatus.user_takeover


def test_auto_accepted_proposal_does_not_park_status(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", AtCap.propose, fake_clock)
    t = data.file_proposal(tmp_db, t.id, field="success", body="success", actor="agent", now=now)
    assert t.stage == "needs_approach"
    assert t.ticket_status is TicketStatus.empty
    status_events = _events(tmp_db, cfg, t.id, EventKind.ticket_status_changed)
    assert status_events == []


def test_current_proposal_with_recap_parks_both_atomically(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="success proposal",
        recap="worker recap",
        actor="agent",
        now=now,
    )

    assert t.stage == "needs_success"
    assert t.recap == "worker recap"
    assert fields_codec.get_slot(t.fields, "success").proposal is not None
    assert fields_codec.get_slot(t.fields, "success").proposal.body == "success proposal"
    assert t.ticket_status is TicketStatus.awaiting_approval
    assert [e.kind for e in _events(tmp_db, cfg, t.id)] == [
        EventKind.ticket_created.value,
        EventKind.proposal_filed.value,
        EventKind.recap_updated.value,
        EventKind.ticket_status_changed.value,
    ]


def test_current_proposal_with_recap_inferrs_current_auto_accept_field(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", AtCap.propose, fake_clock)

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="success proposal",
        recap="first recap",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_approach"
    assert fields_codec.get_slot(t.fields, "success").value == "success proposal"
    assert t.recap == "first recap"

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="approach proposal",
        recap="second recap",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_plan"
    assert fields_codec.get_slot(t.fields, "approach").value == "approach proposal"
    assert t.recap == "second recap"


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
    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 1

    t = data.file_proposal(
        tmp_db, t.id, field="approach", body="approach body", actor="agent", now=now
    )
    assert t.stage == "needs_plan"
    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 2

    t = data.file_proposal(tmp_db, t.id, field="plan", body="plan body", actor="agent", now=now)
    assert t.stage == "needs_implementation"
    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 3

    t = data.file_proposal(
        tmp_db,
        t.id,
        field="implementation",
        body="implementation body",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_closeout"
    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 4

    changes = _events(tmp_db, cfg, t.id, EventKind.stage_changed)
    assert [(e.payload["from_stage"], e.payload["to_stage"]) for e in changes] == [
        ("needs_success", "needs_approach"),
        ("needs_approach", "needs_plan"),
        ("needs_plan", "needs_implementation"),
        ("needs_implementation", "needs_closeout"),
    ]
    for e in changes:
        assert e.payload["cause"] == "auto_accept"
        assert set(e.payload.keys()) == {"from_stage", "to_stage", "cause"}
    for e in _events(tmp_db, cfg, t.id, EventKind.proposal_accepted):
        assert e.payload["resolved_by"] == "auto"
        assert e.payload["edited"] is False


def test_a03_ceiling_auto_accept_until_cap_then_pending(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", AtCap.propose, fake_clock)

    t = data.file_proposal(tmp_db, t.id, field="success", body="s", actor="agent", now=now)
    assert t.stage == "needs_approach"
    t = data.file_proposal(tmp_db, t.id, field="approach", body="a", actor="agent", now=now)
    assert t.stage == "needs_plan"

    t = data.file_proposal(tmp_db, t.id, field="plan", body="plan body", actor="agent", now=now)
    assert t.stage == "needs_plan"
    assert fields_codec.get_slot(t.fields, "plan").value is None
    assert fields_codec.get_slot(t.fields, "plan").proposal is not None
    assert fields_codec.get_slot(t.fields, "plan").proposal.body == "plan body"
    assert fields_codec.get_slot(t.fields, "plan").proposal.proposed_by == "agent"
    assert (
        machine.has_pending_gating_proposal(
            t.stage,
            t.fields,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
        is True
    )

    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 2
    filed = _events(tmp_db, cfg, t.id, EventKind.proposal_filed)
    assert len(filed) == 1
    assert filed[0].payload == {"field": "plan", "body": "plan body", "proposed_by": "agent"}


def test_a04_at_cap_stop_vs_propose(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    a = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, a, "needs_approach", AtCap.propose, fake_clock)
    a = data.file_proposal(tmp_db, a.id, field="success", body="s", actor="agent", now=now)
    assert a.stage == "needs_approach"
    _scope(tmp_db, a, "needs_approach", AtCap.stop, fake_clock)
    count_at_stop = len(_events(tmp_db, cfg, a.id))

    with pytest.raises(PlannerError) as exc_gating:
        data.file_proposal(tmp_db, a.id, field="approach", body="x", actor="agent", now=now)
    assert exc_gating.value.code is ErrorCode.at_cap_stop
    assert exc_gating.value.detail["gating_field"] == "approach"

    with pytest.raises(PlannerError) as exc_non_gating:
        data.file_proposal(tmp_db, a.id, field="plan", body="x", actor="agent", now=now)
    assert exc_non_gating.value.code is ErrorCode.at_cap_stop

    a = data.read_ticket(tmp_db, a.id)
    assert a.stage == "needs_approach"
    assert fields_codec.get_slot(a.fields, "approach").proposal is None
    assert len(_events(tmp_db, cfg, a.id)) == count_at_stop

    _scope(tmp_db, a, "needs_approach", AtCap.propose, fake_clock)
    a = data.file_proposal(
        tmp_db, a.id, field="approach", body="approach draft", actor="agent", now=now
    )
    assert a.stage == "needs_approach"
    assert fields_codec.get_slot(a.fields, "approach").proposal is not None
    assert fields_codec.get_slot(a.fields, "approach").proposal.body == "approach draft"
    assert fields_codec.get_slot(a.fields, "approach").value is None

    count_before = len(_events(tmp_db, cfg, a.id))
    with pytest.raises(PlannerError) as exc_propose_non_gating:
        data.file_proposal(tmp_db, a.id, field="plan", body="x", actor="agent", now=now)
    assert exc_propose_non_gating.value.code is ErrorCode.validation
    a = data.read_ticket(tmp_db, a.id)
    assert a.stage == "needs_approach"
    assert len(_events(tmp_db, cfg, a.id)) == count_before

    b = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, b, "needs_plan", AtCap.stop, fake_clock)
    c = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, c, "needs_plan", AtCap.propose, fake_clock)

    b = data.file_proposal(tmp_db, b.id, field="success", body="s", actor="agent", now=now)
    c = data.file_proposal(tmp_db, c.id, field="success", body="s", actor="agent", now=now)
    assert b.stage == "needs_approach"
    assert c.stage == "needs_approach"
    assert (
        fields_codec.get_slot(b.fields, "success").value
        == fields_codec.get_slot(c.fields, "success").value
        == "s"
    )

    b = data.file_proposal(tmp_db, b.id, field="plan", body="p", actor="agent", now=now)
    c = data.file_proposal(tmp_db, c.id, field="plan", body="p", actor="agent", now=now)
    b_plan = fields_codec.get_slot(b.fields, "plan")
    c_plan = fields_codec.get_slot(c.fields, "plan")
    assert b_plan.proposal is not None and b_plan.value is None
    assert c_plan.proposal is not None and c_plan.value is None
    assert b.stage == "needs_approach"
    assert c.stage == "needs_approach"


def test_a05_one_pending_proposal_per_field_supersede(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    t = data.file_proposal(tmp_db, t.id, field="success", body="first body", actor="agent", now=now)
    assert fields_codec.get_slot(t.fields, "success").proposal is not None
    assert fields_codec.get_slot(t.fields, "success").proposal.body == "first body"

    t = data.file_proposal(
        tmp_db, t.id, field="success", body="second body", actor="agent", now=now
    )
    assert fields_codec.get_slot(t.fields, "success").proposal is not None
    assert fields_codec.get_slot(t.fields, "success").proposal.body == "second body"

    superseded = _events(tmp_db, cfg, t.id, EventKind.proposal_superseded)
    assert len(superseded) == 1
    assert superseded[0].payload == {"field": "success", "replaced_body": "first body"}

    filed = _events(tmp_db, cfg, t.id, EventKind.proposal_filed)
    assert len(filed) == 2
    assert superseded[0].id < filed[1].id

    assert t.stage == "needs_success"
    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 0


def test_a06_edit_accept_stores_edited_text(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_proposal(tmp_db, t.id, field="success", body="draft body", actor="agent", now=now)

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

    accepted = _events(tmp_db, cfg, t.id, EventKind.proposal_accepted)
    assert accepted[-1].payload == {
        "field": "success",
        "body": "edited body exactly",
        "resolved_by": "direct",
        "edited": True,
    }

    assert t.stage == "needs_approach"
    changed = _events(tmp_db, cfg, t.id, EventKind.stage_changed)
    assert changed[-1].payload == {
        "from_stage": "needs_success",
        "to_stage": "needs_approach",
        "cause": "direct_accept",
    }
    assert t.ceiling == "needs_approach"
    assert t.at_cap is AtCap.propose
    scope = _events(tmp_db, cfg, t.id, EventKind.scope_changed)
    assert scope[-1].payload == {
        "ceiling": "needs_approach",
        "at_cap": "propose",
        "cause": "onward_scope",
    }


def test_a07_closeout_routing(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
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
        t1 = data.file_proposal(tmp_db, t1.id, field=f, body=body, actor="agent", now=now)
    assert t1.stage == "needs_closeout"
    t1 = data.file_proposal(tmp_db, t1.id, field="closeout", body="c", actor="agent", now=now)
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
    changed = _events(tmp_db, cfg, t1.id, EventKind.stage_changed)
    assert changed[-1].payload == {
        "from_stage": "needs_closeout",
        "to_stage": "done",
        "cause": "direct_accept",
    }
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
        t2 = data.file_proposal(tmp_db, t2.id, field=f, body=body, actor="agent", now=now)
    assert t2.stage == "done"
    seq = [
        (e.payload["from_stage"], e.payload["to_stage"])
        for e in _events(tmp_db, cfg, t2.id, EventKind.stage_changed)
    ]
    assert seq == [
        ("needs_success", "needs_approach"),
        ("needs_approach", "needs_plan"),
        ("needs_plan", "needs_implementation"),
        ("needs_implementation", "needs_closeout"),
        ("needs_closeout", "done"),
    ]

    # Ceiling below needs_closeout: the closeout proposal stays pending until the
    # ceiling is raised and it is explicitly accepted.
    t3 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t3, "needs_implementation", AtCap.propose, fake_clock)
    for f, body in [("success", "s"), ("approach", "a"), ("plan", "p")]:
        t3 = data.file_proposal(tmp_db, t3.id, field=f, body=body, actor="agent", now=now)
    assert t3.stage == "needs_implementation"
    t3 = data.file_proposal(tmp_db, t3.id, field="implementation", body="i", actor="agent", now=now)
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


def test_a08_recap_rules(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    with pytest.raises(PlannerError) as exc_early:
        data.write_recap(tmp_db, t.id, body="too early", actor="agent", now=now)
    assert exc_early.value.code is ErrorCode.recap_too_early
    assert data.read_ticket(tmp_db, t.id).recap == ""

    _scope(tmp_db, t, "needs_approach", AtCap.propose, fake_clock)
    t = data.file_proposal(tmp_db, t.id, field="success", body="s", actor="agent", now=now)
    assert t.stage == "needs_approach"

    t = data.write_recap(tmp_db, t.id, body="first recap", actor="agent", now=now)
    assert t.recap == "first recap"
    assert len(_events(tmp_db, cfg, t.id, EventKind.recap_updated)) == 1

    t = data.write_recap(tmp_db, t.id, body="second recap", actor="agent", now=now)
    assert t.recap == "second recap"
    assert len(_events(tmp_db, cfg, t.id, EventKind.recap_updated)) == 2
    assert t.stage == "needs_approach"
    assert len(_events(tmp_db, cfg, t.id, EventKind.stage_changed)) == 1

    t = data.drop_ticket(tmp_db, t.id, actor="human", now=now)
    assert t.stage == "dropped"
    with pytest.raises(PlannerError) as exc_dropped:
        data.write_recap(tmp_db, t.id, body="post-drop recap", actor="agent", now=now)
    assert exc_dropped.value.code is ErrorCode.recap_too_early
    assert exc_dropped.value.detail["stage"] == "dropped"
    assert data.read_ticket(tmp_db, t.id).recap == "second recap"


def test_a13_sprint_assignment_rules(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_test', 'Test sprint', '2026-07-01', '2026-07-12', ?, ?)",
        (now, now),
    )

    standalone = _create(tmp_db, cfg, fake_clock)
    standalone = data.edit_ticket(
        tmp_db,
        standalone.id,
        edit=TicketEdit(sprint_id="sp_test"),
        title_max_chars=TITLE_MAX_CHARS,
        actor="agent",
        now=now,
    )
    assert data.read_ticket(tmp_db, standalone.id).sprint_id == "sp_test"
    upd = _events(tmp_db, cfg, standalone.id, EventKind.ticket_updated)
    assert upd[-1].payload == {"field": "sprint_id", "from": None, "to": "sp_test"}

    tmp_db.execute(
        "INSERT INTO sprint_items (id, title, project_id, sprint_id, created_at, updated_at) "
        "VALUES ('si_test', 'Parent item', 'project_vylo', 'sp_test', ?, ?)",
        (now, now),
    )
    parented = _create(tmp_db, cfg, fake_clock, sprint_item_id="si_test")

    with pytest.raises(PlannerError) as exc:
        data.edit_ticket(
            tmp_db,
            parented.id,
            edit=TicketEdit(sprint_id="sp_test"),
            title_max_chars=TITLE_MAX_CHARS,
            actor="human",
            now=now,
        )
    assert exc.value.code is ErrorCode.sprint_derived
    assert data.read_ticket(tmp_db, parented.id).sprint_id is None
    assert _events(tmp_db, cfg, parented.id, EventKind.ticket_updated) == []

    assert data.get_effective_sprint_id(tmp_db, parented.id) == "sp_test"
    assert data.get_effective_sprint_id(tmp_db, standalone.id) == "sp_test"


def test_x06_title_and_project_edits_log_events(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock, project_id="project_vylo")

    renamed = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(title="Renamed ticket"),
        title_max_chars=TITLE_MAX_CHARS,
        actor="human",
        now=now,
    )
    updated = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(project_id=None),
        title_max_chars=TITLE_MAX_CHARS,
        actor="human",
        now=now,
    )

    assert renamed.title == "Renamed ticket"
    assert updated.project_id is None
    assert [event.payload for event in _events(tmp_db, cfg, ticket.id, EventKind.ticket_updated)][
        -2:
    ] == [
        {"field": "title", "from": "Test ticket", "to": "Renamed ticket"},
        {"field": "project_id", "from": "project_vylo", "to": None},
    ]


def test_x06_project_edit_preserves_parented_error_shape(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    tmp_db.execute(
        "INSERT INTO sprint_items (id, title, project_id, created_at, updated_at) "
        "VALUES ('si_project_parent', 'Parent item', 'project_vylo', ?, ?)",
        (now, now),
    )
    ticket = _create(tmp_db, cfg, fake_clock, sprint_item_id="si_project_parent")

    with pytest.raises(PlannerError) as exc:
        data.edit_ticket(
            tmp_db,
            ticket.id,
            edit=TicketEdit(project_id="project_vylo"),
            title_max_chars=TITLE_MAX_CHARS,
            actor="human",
            now=now,
        )

    assert exc.value.code is ErrorCode.validation
    assert exc.value.message == "project is derived when parented"
    assert data.read_ticket(tmp_db, ticket.id).project_id is None
    assert _events(tmp_db, cfg, ticket.id, EventKind.ticket_updated) == []


def test_a36_onward_scope(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()

    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_proposal(tmp_db, t.id, field="success", body="body", actor="agent", now=now)
    assert fields_codec.get_slot(t.fields, "success").proposal is not None
    count = len(_events(tmp_db, cfg, t.id))

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
    assert fields_codec.get_slot(t.fields, "success").value is None
    assert fields_codec.get_slot(t.fields, "success").proposal is not None
    assert fields_codec.get_slot(t.fields, "success").proposal.body == "body"
    assert t.ceiling == "needs_success"
    assert t.at_cap is AtCap.propose
    assert len(_events(tmp_db, cfg, t.id)) == count

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
    assert len(_events(tmp_db, cfg, t.id)) == count

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
    assert len(_events(tmp_db, cfg, t.id)) == count

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
        data.file_proposal(tmp_db, t.id, field="approach", body="x", actor="agent", now=now)
    assert e_rest.value.code is ErrorCode.at_cap_stop

    t2 = _create(tmp_db, cfg, fake_clock)
    t2 = data.file_proposal(tmp_db, t2.id, field="success", body="body", actor="agent", now=now)
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
    t2 = data.file_proposal(tmp_db, t2.id, field="approach", body="draft", actor="agent", now=now)
    assert fields_codec.get_slot(t2.fields, "approach").proposal is not None
    assert t2.stage == "needs_approach"

    t3 = _create(tmp_db, cfg, fake_clock)
    t3 = data.file_proposal(tmp_db, t3.id, field="success", body="body", actor="agent", now=now)
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
    scope_count_before = len(_events(tmp_db, cfg, t3.id, EventKind.scope_changed))
    t3 = data.file_proposal(tmp_db, t3.id, field="approach", body="a", actor="agent", now=now)
    assert t3.stage == "needs_plan"
    assert t3.ceiling == "needs_plan"
    assert t3.at_cap is AtCap.propose
    assert len(_events(tmp_db, cfg, t3.id, EventKind.scope_changed)) == scope_count_before

    t4 = _create(tmp_db, cfg, fake_clock)
    t4 = _scope(tmp_db, t4, "done", AtCap.propose, fake_clock)
    ceiling_before = t4.ceiling
    at_cap_before = t4.at_cap
    scopes_before = len(_events(tmp_db, cfg, t4.id, EventKind.scope_changed))
    for f, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("closeout", "c"),
    ]:
        t4 = data.file_proposal(tmp_db, t4.id, field=f, body=body, actor="agent", now=now)
    assert t4.stage == "done"
    assert t4.ceiling == ceiling_before
    assert t4.at_cap is at_cap_before
    assert len(_events(tmp_db, cfg, t4.id, EventKind.scope_changed)) == scopes_before


def test_read_ticket_by_session_key(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    """A worker resolves its own ticket from its live session key; unknown key -> not_found."""
    t = _create(tmp_db, cfg, fake_clock)
    tmp_db.execute("UPDATE tickets SET chat_session_key = ? WHERE id = ?", ("sess_abc", t.id))
    assert data.read_ticket_by_session_key(tmp_db, "sess_abc").id == t.id
    with pytest.raises(PlannerError) as exc:
        data.read_ticket_by_session_key(tmp_db, "no_such_session")
    assert exc.value.code is ErrorCode.not_found
