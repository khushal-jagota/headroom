"""Acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 for the tickets engine (SPEC §4.4).

Drives everything through planner.tickets.data against a real temp SQLite DB via
the shared conftest fixtures. No mocks. Assertions pin the frozen states, event
payloads, event order, and error codes from the T04 plan.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from tests.support.principals import OWNER_PRINCIPAL, TEST_TICKET_PRINCIPAL
from tests.support.probe import install_probe_registry, uninstall_probe_registry
from tests.support.ticket_progress import advance_ticket

from planner.core.contracts import LinkKind, Principal, PrincipalKind, Priority
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
        principal=OWNER_PRINCIPAL,
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
        principal=OWNER_PRINCIPAL,
        now=clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    return ticket


def _scope(conn: Connection, t: Ticket, ceiling: str, at_cap: AtCap, clock: TestClock) -> Ticket:
    return data.change_scope(
        conn, t.id, ceiling=ceiling, at_cap=at_cap, principal=OWNER_PRINCIPAL, now=clock.now_unix()
    )


def _claim_ready_worker_step(conn: Connection, ticket_id: str, *, now: int) -> Ticket | None:
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


def test_ticket_guidance_round_trip_keeps_kickoff_separate(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(tmp_db, cfg, fake_clock, kickoff_note="intake direction")
    assert t.field_values.get("kickoff") == "intake direction"

    t = data.edit_field_value(
        tmp_db,
        t.id,
        field="kickoff",
        new_body="updated intake direction",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
    )
    assert t.field_values.get("kickoff") == "updated intake direction"

    t = data.replace_guidance(
        tmp_db,
        t.id,
        body="approach guidance",
        principal=TEST_TICKET_PRINCIPAL,
        now=fake_clock.now_unix(),
    )
    assert t.guidance == "approach guidance"


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
    assert t.field_values.get("kickoff") is None
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "draft kickoff note"
    assert dict(t.field_values) == {}
    assert (t.pending_proposal is not None) is True


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
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="probe",
    )
    overridden = actions.create_ticket(
        tmp_db,
        title="Probe override",
        principal=OWNER_PRINCIPAL,
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
            principal=OWNER_PRINCIPAL,
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
            principal=OWNER_PRINCIPAL,
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
    assert tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == tickets_before


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
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
    )
    assert renamed.stage == "needs_kickoff"
    assert renamed.pending_proposal is not None

    settled = data.accept_proposal(
        tmp_db,
        t.id,
        field="kickoff",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        edited_body="approved note",
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )

    assert settled.stage == "needs_success"
    assert settled.ticket_status is TicketStatus.empty
    assert settled.title == "Approved title"
    assert settled.field_values.get("kickoff") == "approved note"
    assert settled.pending_proposal is None
    assert (settled.pending_proposal is not None) is False


def test_ticket_status_transitions(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
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
            expected_status_revision=claimed.ticket_status_revision,
            now=now,
        )
        is True
    )
    t = data.read_ticket(tmp_db, t.id)
    assert t.ticket_status is TicketStatus.empty

    claimed_again = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed_again is not None
    t = claimed_again
    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="parked",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.ticket_status is TicketStatus.awaiting_approval

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t.ticket_status is TicketStatus.empty

    t = data.take_over_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.empty
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
    t = data.mark_ticket_errored(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.errored

    t = data.drop_ticket(tmp_db, t.id, principal=OWNER_PRINCIPAL, now=now + 1)
    assert t.ticket_status is TicketStatus.empty
    assert tmp_db.execute(
        "SELECT ticket_status FROM tickets WHERE id = ?", (t.id,)
    ).fetchone()[0] == "empty"


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
        expected_status_revision=claimed.ticket_status_revision,
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
            expected_status_revision=claimed.ticket_status_revision,
            now=now + 6,
        )
        is False
    )
    assert data.read_ticket(tmp_db, t.id).ticket_status is TicketStatus.agent


def test_a_paired_owned_stage_records_its_opener_and_returns_to_empty(
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
    assert claimed.ticket_status is TicketStatus.agent

    assert data.release_worker_step_claim(
        tmp_db,
        t.id,
        expected_status=claimed.ticket_status,
        expected_status_revision=claimed.ticket_status_revision,
        now=now + 1,
    )
    assert data.read_ticket(tmp_db, t.id).ticket_status is TicketStatus.empty
    assert tmp_db.execute(
        "SELECT stage FROM ticket_paired_stage_openers WHERE ticket_id = ?", (t.id,)
    ).fetchone()[0] == t.stage


def _park_pending(
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
    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="parked",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.ticket_status is TicketStatus.awaiting_approval
    return t


def test_pending_proposal_accept_rests_the_ticket(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _park_pending(tmp_db, cfg, fake_clock, now)
    data.replace_guidance(
        tmp_db, t.id, body="Keep this boundary", principal=OWNER_PRINCIPAL, now=now
    )
    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t.ticket_status is TicketStatus.empty

    assert t.guidance == "Keep this boundary"


def test_pending_proposal_send_back_reopens_and_clears_proposal(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _park_pending(tmp_db, cfg, fake_clock, now)
    data.replace_guidance(
        tmp_db, t.id, body="Keep this boundary", principal=OWNER_PRINCIPAL, now=now
    )
    t = data.return_for_revision(
        tmp_db,
        t.id,
        message="please revise",
        principal=OWNER_PRINCIPAL,
        now=now,
    )
    assert t.ticket_status is TicketStatus.empty
    assert t.pending_proposal is None
    assert t.field_values.get("success") is None
    assert t.guidance == "Keep this boundary\n\nplease revise"


def test_a02_gating_chain_one_state_per_accept(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_closeout", AtCap.propose, fake_clock)

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="success body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_approach"
    assert t.field_values.get("success") == "success body"
    assert t.pending_proposal is None

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="approach body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_plan"
    assert t.field_values.get("approach") == "approach body"

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="plan body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_implementation"
    assert t.field_values.get("plan") == "plan body"

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="implementation body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_closeout"
    assert t.field_values.get("implementation") == "implementation body"
    assert t.pending_proposal is None


def test_a03_ceiling_auto_accept_until_cap_then_pending(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", AtCap.propose, fake_clock)

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="s",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_approach"
    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="a",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_plan"

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="plan body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_plan"
    assert t.field_values.get("plan") is None
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "plan body"
    assert t.pending_proposal.proposed_by == "worker"
    assert (t.pending_proposal is not None) is True

    assert t.ticket_status is TicketStatus.awaiting_approval


def test_a05_one_pending_proposal_per_ticket_supersede(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="first body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "first body"

    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="second body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "second body"

    assert t.stage == "needs_success"


def test_a06_edit_accept_stores_edited_text(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="draft body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=now,
        edited_body="edited body exactly",
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t.field_values.get("success") == "edited body exactly"
    assert t.pending_proposal is None

    assert t.stage == "needs_approach"
    assert t.ceiling == "needs_approach"
    assert t.at_cap is AtCap.propose


def test_a07_closeout_routing(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()

    # Ceiling stops exactly at needs_closeout: implementation auto-accepts up to
    # needs_closeout, and closeout then routes to done through the ordinary
    # accept machinery (no special-cased manual review step).
    t1 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t1, "needs_closeout", AtCap.propose, fake_clock)
    for _field, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
    ]:
        t1 = data.file_current_proposal_with_recap(
            tmp_db,
            t1.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t1.id),
            now=now,
            recap="Current work",
        )
    assert t1.stage == "needs_closeout"
    t1 = data.file_current_proposal_with_recap(
        tmp_db,
        t1.id,
        body="c",
        principal=Principal(PrincipalKind.ticket, t1.id),
        now=now,
        recap="Current work",
    )
    assert t1.stage == "needs_closeout"
    assert t1.pending_proposal is not None
    assert t1.field_values.get("closeout") is None

    t1 = data.accept_proposal(
        tmp_db,
        t1.id,
        field="closeout",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t1.stage == "done"
    assert t1.field_values.get("closeout") == "c"
    assert t1.ceiling == "done"
    assert t1.at_cap is AtCap.propose

    # Ceiling at done from the start: every field, including closeout, auto-accepts
    # straight through to done.
    t2 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t2, "done", AtCap.propose, fake_clock)
    for _field, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("closeout", "c"),
    ]:
        t2 = data.file_current_proposal_with_recap(
            tmp_db,
            t2.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t2.id),
            now=now,
            recap="Current work",
        )
    assert t2.stage == "done"
    assert all(
        t2.field_values.get(field) == body
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
    for _field, body in [("success", "s"), ("approach", "a"), ("plan", "p")]:
        t3 = data.file_current_proposal_with_recap(
            tmp_db,
            t3.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t3.id),
            now=now,
            recap="Current work",
        )
    assert t3.stage == "needs_implementation"
    t3 = data.file_current_proposal_with_recap(
        tmp_db,
        t3.id,
        body="i",
        principal=Principal(PrincipalKind.ticket, t3.id),
        now=now,
        recap="Current work",
    )
    assert t3.stage == "needs_implementation"
    assert t3.pending_proposal is not None

    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field="implementation",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling="done",
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t3.stage == "needs_closeout"


def test_a08_recap_rules(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    # Recap is never blocked: writable from the first worker stage.
    assert t.stage == "needs_success"
    t = data.write_recap(tmp_db, t.id, body="first recap", principal=TEST_TICKET_PRINCIPAL, now=now)
    assert t.recap == "first recap"
    assert t.stage == "needs_success"

    _scope(tmp_db, t, "needs_approach", AtCap.propose, fake_clock)
    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="s",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.stage == "needs_approach"

    t = data.write_recap(
        tmp_db, t.id, body="second recap", principal=TEST_TICKET_PRINCIPAL, now=now
    )
    assert t.recap == "second recap"
    assert t.stage == "needs_approach"

    # And still writable on a terminal ticket.
    t = data.drop_ticket(tmp_db, t.id, principal=OWNER_PRINCIPAL, now=now)
    assert t.stage == "dropped"
    t = data.write_recap(
        tmp_db, t.id, body="post-drop recap", principal=TEST_TICKET_PRINCIPAL, now=now
    )
    assert t.recap == "post-drop recap"


def test_a36_onward_scope(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()

    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_current_proposal_with_recap(
        tmp_db,
        t.id,
        body="body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
        recap="Current work",
    )
    assert t.pending_proposal is not None
    row_before = _ticket_row(tmp_db, t.id)

    with pytest.raises(PlannerError) as e_missing_ceiling:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling=None,
            at_cap=AtCap.stop,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_missing_ceiling.value.code is ErrorCode.scope_missing
    with pytest.raises(PlannerError) as e_missing_at_cap:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling=NO_FURTHER,
            at_cap=None,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_missing_at_cap.value.code is ErrorCode.scope_missing

    t = data.read_ticket(tmp_db, t.id)
    assert t.stage == "needs_success"
    assert t.field_values.get("success") is None
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "body"
    assert t.ceiling == "needs_success"
    assert t.at_cap is AtCap.propose
    assert _ticket_row(tmp_db, t.id) == row_before

    with pytest.raises(PlannerError) as e_before:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling="needs_success",
            at_cap=AtCap.propose,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_before.value.code is ErrorCode.scope_invalid
    with pytest.raises(PlannerError) as e_dropped:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling="dropped",
            at_cap=AtCap.propose,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_dropped.value.code is ErrorCode.scope_invalid
    assert data.read_ticket(tmp_db, t.id).stage == "needs_success"
    assert _ticket_row(tmp_db, t.id) == row_before

    with pytest.raises(PlannerError) as e_agent:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success",
            principal=TEST_TICKET_PRINCIPAL,
            now=now,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_agent.value.code is ErrorCode.agent_forbidden
    assert _ticket_row(tmp_db, t.id) == row_before

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t.stage == "needs_approach"
    assert t.ceiling == "needs_approach"
    assert t.at_cap is AtCap.stop
    with pytest.raises(PlannerError) as e_rest:
        data.file_current_proposal_with_recap(
            tmp_db,
            t.id,
            body="x",
            principal=Principal(PrincipalKind.ticket, t.id),
            now=now,
            recap="Current work",
        )
    assert e_rest.value.code is ErrorCode.at_cap_stop

    t2 = _create(tmp_db, cfg, fake_clock)
    t2 = data.file_current_proposal_with_recap(
        tmp_db,
        t2.id,
        body="body",
        principal=Principal(PrincipalKind.ticket, t2.id),
        now=now,
        recap="Current work",
    )
    t2 = data.accept_proposal(
        tmp_db,
        t2.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t2.stage == "needs_approach"
    t2 = data.file_current_proposal_with_recap(
        tmp_db,
        t2.id,
        body="draft",
        principal=Principal(PrincipalKind.ticket, t2.id),
        now=now,
        recap="Current work",
    )
    assert t2.pending_proposal is not None
    assert t2.stage == "needs_approach"

    t3 = _create(tmp_db, cfg, fake_clock)
    t3 = data.file_current_proposal_with_recap(
        tmp_db,
        t3.id,
        body="body",
        principal=Principal(PrincipalKind.ticket, t3.id),
        now=now,
        recap="Current work",
    )
    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling="needs_plan",
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t3.ceiling == "needs_plan"
    t3 = data.file_current_proposal_with_recap(
        tmp_db,
        t3.id,
        body="a",
        principal=Principal(PrincipalKind.ticket, t3.id),
        now=now,
        recap="Current work",
    )
    assert t3.stage == "needs_plan"
    assert t3.ceiling == "needs_plan"
    assert t3.at_cap is AtCap.propose

    t4 = _create(tmp_db, cfg, fake_clock)
    t4 = _scope(tmp_db, t4, "done", AtCap.propose, fake_clock)
    ceiling_before = t4.ceiling
    at_cap_before = t4.at_cap
    for _field, body in [
        ("success", "s"),
        ("approach", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("closeout", "c"),
    ]:
        t4 = data.file_current_proposal_with_recap(
            tmp_db,
            t4.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t4.id),
            now=now,
            recap="Current work",
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
    tmp_db.execute("UPDATE tickets SET conversation_id = ? WHERE id = ?", ("sess_abc", t.id))
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

    advance_ticket(tmp_db, first.id, new_stage="done", principal=OWNER_PRINCIPAL, now=now)
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked

    advance_ticket(tmp_db, second.id, new_stage="done", principal=OWNER_PRINCIPAL, now=now)
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
        advance_ticket(tmp_db, blocker.id, new_stage="done", principal=OWNER_PRINCIPAL, now=now)
    else:
        draft = "  Earlier draft\n\n```text\nΔ unapproved\n```\n"
        data.file_current_proposal_with_recap(
            tmp_db,
            blocker.id,
            body=draft,
            recap="work",
            principal=Principal(PrincipalKind.ticket, blocker.id),
            now=now,
        )
        dropped = data.drop_ticket(tmp_db, blocker.id, principal=OWNER_PRINCIPAL, now=now)
        assert dropped.pending_proposal is None
        assert draft in dropped.archived_field_content
        assert "Unapproved proposal" in dropped.archived_field_content
        assert dropped.field_values == blocker.field_values

    assert _links_from(tmp_db, blocker.id) == []
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.empty


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
    blocker = data.file_current_proposal_with_recap(
        tmp_db,
        blocker.id,
        body="retain draft",
        recap="work",
        principal=Principal(PrincipalKind.ticket, blocker.id),
        now=now,
    )
    blocker_row_before = _ticket_row(tmp_db, blocker.id)
    stage_before = blocker.stage
    target_row_before = _ticket_row(tmp_db, target.id)

    def failing_settle(conn: Connection, target_id: str, now: int) -> None:
        raise RuntimeError("settlement failed mid-completion")

    monkeypatch.setattr(data, "settle_blocked_standin_for_link_target", failing_settle)
    with pytest.raises(RuntimeError, match="mid-completion"):
        data.drop_ticket(tmp_db, blocker.id, principal=OWNER_PRINCIPAL, now=now)

    assert data.read_ticket(tmp_db, blocker.id).stage == stage_before
    assert _ticket_row(tmp_db, blocker.id) == blocker_row_before
    assert _links_from(tmp_db, blocker.id) == [(target.id, "blocks")]
    assert data.read_ticket(tmp_db, target.id).ticket_status is TicketStatus.blocked
    assert _ticket_row(tmp_db, target.id) == target_row_before


def test_delete_admission_uses_the_exact_sprint_item_principal(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    project = projects_data.create_project(
        tmp_db,
        name="Parent project",
        priority=Priority.P3,
        now=fake_clock.now_unix(),
    )
    item = sprints_data.create_item(
        tmp_db,
        title="Parent item",
        project_id=project.id,
        clock=fake_clock,
    )
    ticket = _create(
        tmp_db,
        cfg,
        fake_clock,
        sprint_item_id=item.id,
        settle_kickoff=False,
    )

    with pytest.raises(PlannerError) as worker_error:
        data.delete_ticket(
            tmp_db,
            ticket.id,
            principal=Principal(PrincipalKind.ticket, ticket.id),
            now=fake_clock.now_unix(),
        )
    assert worker_error.value.code is ErrorCode.agent_forbidden

    with pytest.raises(PlannerError) as wrong_supervisor_error:
        data.delete_ticket(
            tmp_db,
            ticket.id,
            principal=Principal(PrincipalKind.sprint_item, "item_other"),
            supervisor_sprint_item_id=item.id,
            now=fake_clock.now_unix(),
        )
    assert wrong_supervisor_error.value.code is ErrorCode.agent_forbidden

    data.delete_ticket(
        tmp_db,
        ticket.id,
        principal=Principal(PrincipalKind.sprint_item, item.id),
        supervisor_sprint_item_id=item.id,
        now=fake_clock.now_unix(),
    )
    assert tmp_db.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket.id,)).fetchone() is None


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
        ticket = data.file_current_proposal_with_recap(
            tmp_db,
            ticket.id,
            body=f"{field} body",
            principal=Principal(PrincipalKind.ticket, ticket.id),
            now=now,
            recap="Current work",
        )
    assert ticket.stage == "needs_plan"
    assert ticket.ticket_status is TicketStatus.awaiting_approval

    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="plan",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )

    assert ticket.stage == "needs_implementation"
    assert ticket.ticket_status is TicketStatus.empty


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
        ticket = data.file_current_proposal_with_recap(
            tmp_db,
            ticket.id,
            body=f"{field} body",
            principal=Principal(PrincipalKind.ticket, ticket.id),
            now=now,
            recap="Current work",
        )
    started = _claim_ready_worker_step(tmp_db, ticket.id, now=now)
    assert started is not None

    ticket = data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="plan body",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=now,
        recap="Current work",
    )
    assert ticket.stage == "needs_implementation"
    assert ticket.ticket_status is TicketStatus.empty
