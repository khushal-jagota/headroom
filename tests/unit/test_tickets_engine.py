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


def test_ticket_user_note_replace_and_append_are_explicit(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, kickoff_note="keep this value")

    replaced = data.replace_field_user_note(
        tmp_db,
        t.id,
        field="approach",
        user_note="first guidance",
        actor="human",
        now=fake_clock.now_unix(),
    )
    appended = data.append_field_user_note(
        tmp_db,
        t.id,
        field="approach",
        user_note="second guidance",
        actor="agent",
        now=fake_clock.now_unix(),
    )
    empty_append = data.append_field_user_note(
        tmp_db,
        t.id,
        field="approach",
        user_note="",
        actor="agent",
        now=fake_clock.now_unix(),
    )

    assert fields_codec.get_slot(replaced.fields, "approach").user_note == "first guidance"
    assert (
        fields_codec.get_slot(appended.fields, "approach").user_note
        == "first guidance\n\nsecond guidance"
    )
    assert fields_codec.get_slot(empty_append.fields, "approach").user_note == (
        "first guidance\n\nsecond guidance"
    )
    assert fields_codec.get_slot(empty_append.fields, "kickoff").value == "keep this value"

    cleared = data.replace_field_user_note(
        tmp_db,
        t.id,
        field="approach",
        user_note=None,
        actor="human",
        now=fake_clock.now_unix(),
    )
    appended_after_clear = data.append_field_user_note(
        tmp_db,
        t.id,
        field="approach",
        user_note="new guidance",
        actor="agent",
        now=fake_clock.now_unix(),
    )

    assert fields_codec.get_slot(cleared.fields, "approach").user_note is None
    assert (
        fields_codec.get_slot(appended_after_clear.fields, "approach").user_note
        == "new guidance"
    )


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


def test_review_exposes_kickoff_as_ordinary_ticket_decision(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)
    day_id = "day_2026-07-04"
    days_data.add_day_ticket(tmp_db, day_id, t.id, fake_clock.now_unix())

    review = ticket_views.review_view(tmp_db, day_id=day_id)

    kickoff_slot = fields_codec.get_slot(t.fields, "kickoff")
    assert kickoff_slot.proposal is not None
    assert review["items"] == [
        {
            "review_item_type": "proposal",
            "ticket_id": t.id,
            "field": "kickoff",
            "title": t.title,
            "waiting_since": kickoff_slot.proposal.created_at,
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


def test_accept_kickoff_field_leaves_ticket_ready(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, settle_kickoff=False)

    settled = data.accept_proposal(
        tmp_db,
        t.id,
        field="kickoff",
        actor="human",
        now=fake_clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )

    assert settled.stage == "needs_success"
    days_data.add_day_ticket(
        tmp_db, _AUTOMATIC_PLANNING_DAY_ID, settled.id, fake_clock.now_unix()
    )
    assert (
        worker_step_readiness.is_ready_for_worker_step(
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


def test_paired_exit_re_propose_returns_to_awaiting_approval(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _park_paired(tmp_db, cfg, fake_clock, now)
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="revised", actor="agent", now=now
    )
    assert t.ticket_status is TicketStatus.awaiting_approval


def test_paired_exit_accept_rests_the_ticket(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _park_paired(tmp_db, cfg, fake_clock, now)
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


def test_take_over_from_paired_re_derives_the_user_status(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # paired is no longer excluded from ownership-change re-derivation: a resting
    # paired-owned Ticket must follow the takeover to `user`.
    now = fake_clock.now_unix()
    t = _park_paired(tmp_db, cfg, fake_clock, now)
    t = data.take_over_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.user


def test_an_errored_ticket_stays_errored_and_is_never_claimed(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _claim_ready_worker_step(tmp_db, t.id, now=now)
    # The Ticket keeps the conversation it named through erroring and release: a worker
    # that fell over is still the worker that was talking there.
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'conversation-errored' WHERE id = ?",
        (t.id,),
    )
    tmp_db.commit()
    t = data.mark_ticket_errored(tmp_db, t.id, error="boom", now=now)
    assert t.ticket_status is TicketStatus.errored
    assert t.conversation_id == "conversation-errored"

    t = data.release_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.errored
    assert _claim_ready_worker_step(tmp_db, t.id, now=now) is None
    assert t.conversation_id == "conversation-errored"
    assert t.backend_error == "boom"


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


def test_current_worker_plan_proposal_derives_user_owned_implementation_status(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = _create(tmp_db, cfg, fake_clock)
    data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_implementation",
        ownership_mode=StageOwnershipMode.user,
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

    ticket = data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="plan body",
        recap="Plan ready for implementation.",
        actor="agent",
        now=now,
    )

    assert ticket.stage == "needs_implementation"
    assert ticket.ticket_status is TicketStatus.user


def test_auto_accepted_proposal_does_not_park_status(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", AtCap.propose, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field="success", body="success", actor="agent", now=now
    )
    assert t.stage == "needs_approach"
    assert t.ticket_status is TicketStatus.empty


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
    success_slot = fields_codec.get_slot(t.fields, "success")
    assert success_slot.proposal is not None
    assert success_slot.proposal.body == "success proposal"
    assert t.ticket_status is TicketStatus.awaiting_approval


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


def test_a04_at_cap_stop_vs_propose(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    a = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, a, "needs_approach", AtCap.propose, fake_clock)
    a = data.file_proposal(
        tmp_db, a.id, field="success", body="s", actor="agent", now=now
    )
    assert a.stage == "needs_approach"
    _scope(tmp_db, a, "needs_approach", AtCap.stop, fake_clock)
    row_at_stop = _ticket_row(tmp_db, a.id)

    with pytest.raises(PlannerError) as exc_gating:
        data.file_proposal(
            tmp_db, a.id, field="approach", body="x", actor="agent", now=now
        )
    assert exc_gating.value.code is ErrorCode.at_cap_stop
    assert exc_gating.value.detail["gating_field"] == "approach"

    with pytest.raises(PlannerError) as exc_non_gating:
        data.file_proposal(tmp_db, a.id, field="plan", body="x", actor="agent", now=now)
    assert exc_non_gating.value.code is ErrorCode.at_cap_stop

    a = data.read_ticket(tmp_db, a.id)
    assert a.stage == "needs_approach"
    assert fields_codec.get_slot(a.fields, "approach").proposal is None
    assert _ticket_row(tmp_db, a.id) == row_at_stop

    _scope(tmp_db, a, "needs_approach", AtCap.propose, fake_clock)
    a = data.file_proposal(
        tmp_db, a.id, field="approach", body="approach draft", actor="agent", now=now
    )
    assert a.stage == "needs_approach"
    approach_slot = fields_codec.get_slot(a.fields, "approach")
    assert approach_slot.proposal is not None
    assert approach_slot.proposal.body == "approach draft"
    assert approach_slot.value is None

    row_before = _ticket_row(tmp_db, a.id)
    with pytest.raises(PlannerError) as exc_propose_non_gating:
        data.file_proposal(tmp_db, a.id, field="plan", body="x", actor="agent", now=now)
    assert exc_propose_non_gating.value.code is ErrorCode.validation
    a = data.read_ticket(tmp_db, a.id)
    assert a.stage == "needs_approach"
    assert _ticket_row(tmp_db, a.id) == row_before

    b = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, b, "needs_plan", AtCap.stop, fake_clock)
    c = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, c, "needs_plan", AtCap.propose, fake_clock)

    b = data.file_proposal(
        tmp_db, b.id, field="success", body="s", actor="agent", now=now
    )
    c = data.file_proposal(
        tmp_db, c.id, field="success", body="s", actor="agent", now=now
    )
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


def test_x06_title_and_project_edits_land_on_the_ticket_row(
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
    stored = data.read_ticket(tmp_db, ticket.id)
    assert stored.title == "Renamed ticket"
    assert stored.project_id is None


def test_x06_parented_project_edit_requires_a_coherent_tuple(
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
            edit=TicketEdit(project_id="project_other"),
            title_max_chars=TITLE_MAX_CHARS,
            actor="human",
            now=now,
        )

    assert exc.value.code is ErrorCode.validation
    assert exc.value.message == "ticket placement does not match sprint item"
    assert data.read_ticket(tmp_db, ticket.id).project_id == "project_vylo"


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


def test_link_add_and_remove_settle_the_blocked_standin(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    assert target.ticket_status is TicketStatus.empty

    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked

    actions.remove_link(tmp_db, blocker.id, target.id, LinkKind.blocks, now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty


def test_repeated_link_add_is_rejected_and_leaves_the_target_blocked(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)

    with pytest.raises(PlannerError) as excinfo:
        _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    assert excinfo.value.code is ErrorCode.link_invalid
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked


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


def test_deleting_a_blocker_frees_its_target(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)

    data.delete_ticket(tmp_db, blocker.id, actor="human", now=now)

    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty


def test_completion_cleanup_settles_ticket_targets_and_skips_sprint_item_targets(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    tmp_db.execute(
        "INSERT INTO sprint_items (id, title, project_id, created_at, updated_at) "
        "VALUES ('si_blocked', 'Blocked item', 'project_vylo', ?, ?)",
        (now, now),
    )
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    _block(tmp_db, blocker_id=blocker.id, target_id="si_blocked", now=now)
    assert len(_links_from(tmp_db, blocker.id)) == 2

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


def test_a_ticket_with_a_live_blocker_comes_to_rest_at_blocked(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)

    tmp_db.execute(
        "UPDATE tickets SET ticket_status = ?, ticket_status_changed_at = ? WHERE id = ?",
        (TicketStatus.agent.value, now, target.id),
    )
    assert data.release_worker_step_claim(
        tmp_db,
        target.id,
        expected_status=TicketStatus.agent,
        expected_status_changed_at=now,
        now=now,
    )
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked


def test_creation_with_blockers_parks_at_kickoff_then_rests_at_blocked(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    created = data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Blocked from birth",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        blocked_by_ticket_ids=[blocker.id],
    )
    # A filed kickoff proposal owns the status; blocked only stands in for empty.
    assert created.ticket_status is TicketStatus.awaiting_approval

    accepted = data.accept_proposal(
        tmp_db,
        created.id,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert accepted.stage == "needs_success"
    assert accepted.ticket_status is TicketStatus.blocked


def test_external_work_creation_with_blockers_ends_blocked(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    created = data.create_ticket_from_external_work(
        tmp_db,
        worker_type="coding",
        title="Already done elsewhere",
        kickoff_note="external note",
        target_stage="needs_approach",
        provided_values={"success": "success"},
        actor="chief",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        blocked_by_ticket_ids=[blocker.id],
    )
    assert created.stage == "needs_approach"
    assert created.ticket_status is TicketStatus.blocked


def test_auto_accepted_closeout_frees_a_blocked_target(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    _scope(tmp_db, blocker, "done", AtCap.propose, fake_clock)
    for field, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
    ]:
        data.file_proposal(
            tmp_db, blocker.id, field=field, body=body, actor="agent", now=now
        )
    assert data.read_ticket(tmp_db, blocker.id).stage == "needs_closeout"

    target = _create(tmp_db, cfg, fake_clock, title="Target")
    _block(tmp_db, blocker_id=blocker.id, target_id=target.id, now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked

    closed = data.file_current_proposal_with_recap(
        tmp_db,
        blocker.id,
        body="c",
        recap="closing recap",
        actor="agent",
        now=now,
    )

    assert closed.stage == "done"
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty
    assert _links_from(tmp_db, blocker.id) == []


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


def test_a_rejected_reopen_leaves_everything_untouched(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # A reopen that would close an active cycle is refused before anything is
    # written: stage, links, and statuses all stay exactly as they were.
    now = fake_clock.now_unix()
    first = _create(tmp_db, cfg, fake_clock, title="First")
    second = _create(tmp_db, cfg, fake_clock, title="Second")
    data.set_stage(tmp_db, first.id, new_stage="done", actor="human", now=now)
    # Legal today: the done source cannot close an active cycle.
    _block(tmp_db, blocker_id=first.id, target_id=second.id, now=now)
    _block(tmp_db, blocker_id=second.id, target_id=first.id, now=now)
    assert data.read_ticket(tmp_db, first.id).ticket_status is TicketStatus.blocked
    second_row_before = _ticket_row(tmp_db, second.id)

    with pytest.raises(PlannerError) as excinfo:
        data.set_stage(
            tmp_db, first.id, new_stage="needs_success", actor="human", now=now
        )
    assert excinfo.value.code is ErrorCode.link_cycle

    assert data.read_ticket(tmp_db, first.id).stage == "done"
    assert data.read_ticket(tmp_db, first.id).ticket_status is TicketStatus.blocked
    assert data.read_ticket(tmp_db, second.id).ticket_status is TicketStatus.empty
    assert _links_from(tmp_db, first.id) == [(second.id, "blocks")]
    assert _ticket_row(tmp_db, second.id) == second_row_before
