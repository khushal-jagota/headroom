"""Explicit deferral preserves work; retirement preserves its full provenance."""

import json
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal

from planner.core.contracts import PlannerError
from planner.list_reads.contracts import ListPageRequest
from planner.runtime.worker_step_readiness import is_ready_for_worker_step
from planner.tickets import data, views
from planner.tickets.contracts import Ticket, TicketListFilters, WorkerStepClaim
from planner.worker_types.configuration import configured_worker_type_registry


def create(conn: Connection) -> str:
    return data.create_ticket(
        conn,
        worker_type="coding",
        title="Deferred work",
        principal=OWNER_PRINCIPAL,
        now=10,
        title_max_chars=200,
        kickoff_note="Agreed brief",
    ).id


def backlog(conn: Connection, tid: str, value: bool = True) -> Ticket:
    return data.set_ticket_backlog(
        conn, tid, is_backlogged=value, principal=OWNER_PRINCIPAL, now=20
    )


def retire(conn: Connection, tid: str, *, expected_record_sha256: str | None = None) -> Ticket:
    return data.retire_ticket(
        conn,
        tid,
        reason="Superseded",
        principal=OWNER_PRINCIPAL,
        now=30,
        expected_record_sha256=expected_record_sha256,
    )


def test_backlog_preserves_pending_proposal_and_direct_authority(tmp_db: Connection) -> None:
    tid = create(tmp_db)
    before = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
    deferred = backlog(tmp_db, tid)
    after = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
    assert after == {**before, "is_backlogged": 1, "updated_at": 20}
    assert deferred.pending_proposal == data.read_ticket(tmp_db, tid).pending_proposal
    definition = configured_worker_type_registry().require("coding")
    assert not is_ready_for_worker_step(tmp_db, deferred, worker_type_definition=definition)
    advanced = data.accept_proposal(
        tmp_db,
        tid,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=21,
        next_ceiling="needs_implementation",
        next_holder=OWNER_PRINCIPAL,
    )
    assert advanced.is_backlogged and advanced.stage == "needs_success_condition"
    assert (
        data.claim_ticket_for_worker_step(
            tmp_db, tid, readiness_check=is_ready_for_worker_step, now=22
        )
        is None
    )
    resumed = backlog(tmp_db, tid, False)
    assert resumed.stage == advanced.stage and resumed.pending_proposal is None
    assert is_ready_for_worker_step(tmp_db, resumed, worker_type_definition=definition)
    assert (
        data.claim_ticket_for_worker_step(
            tmp_db, tid, readiness_check=is_ready_for_worker_step, now=23
        )
        is not None
    )
    claimed = data.read_ticket(tmp_db, tid)
    deferred_again = backlog(tmp_db, tid)
    assert deferred_again.worker_step_claim is WorkerStepClaim.out
    assert deferred_again.worker_step_claim_revision == claimed.worker_step_claim_revision
    with pytest.raises(PlannerError, match="reconciliation"):
        retire(tmp_db, tid)


def test_retirement_preserves_full_record_and_refuses_stale_decisions(tmp_db: Connection) -> None:
    tid = create(tmp_db)
    original = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
    retired = retire(tmp_db, tid)
    assert retired.stage == "cancelled" and retired.pending_proposal is None
    provenance = json.loads(retired.field_values["retirement"])
    assert provenance["original_record"] == original
    assert provenance["original_pending_proposal_json"] == original["pending_proposal"]
    assert provenance["retired_by"] == {"kind": "owner", "id": "owner"}
    assert retire(tmp_db, tid) == retired
    with pytest.raises(PlannerError):
        data.accept_proposal(
            tmp_db,
            tid,
            field="brief",
            principal=OWNER_PRINCIPAL,
            now=31,
            next_ceiling="needs_success_condition",
            next_holder=OWNER_PRINCIPAL,
            expected_proposal_revision=original["pending_proposal_revision"],
        )
    with pytest.raises(PlannerError):
        data.reject_proposal(tmp_db, tid, principal=OWNER_PRINCIPAL, now=31, message=None)
    with pytest.raises(PlannerError):
        backlog(tmp_db, tid, False)
    definition = configured_worker_type_registry().require("coding")
    assert definition.is_terminal("cancelled")
    assert "cancelled" not in definition.stage_ids()
    assert "cancelled" not in definition.ceiling_range()


def test_hash_guard_detects_same_second_changes_and_authority_is_unchanged(
    tmp_db: Connection,
) -> None:
    tid = create(tmp_db)
    snapshot = views.ticket_detail(tmp_db, tid, 10)
    tmp_db.execute("UPDATE tickets SET title='Changed in same second' WHERE id=?", (tid,))
    tmp_db.commit()
    with pytest.raises(PlannerError, match="changed"):
        retire(tmp_db, tid, expected_record_sha256=snapshot["record_sha256"])
    with pytest.raises(PlannerError):
        data.set_ticket_backlog(
            tmp_db, tid, is_backlogged=True, principal=ticket_principal(tid), now=20
        )
    assert data.read_ticket(tmp_db, tid).stage == "needs_brief"


def test_bounded_summary_can_filter_deferral_without_conflating_placement(
    tmp_db: Connection,
) -> None:
    deferred = create(tmp_db)
    active = create(tmp_db)
    backlog(tmp_db, deferred)
    page = views.list_ticket_summaries(
        tmp_db,
        page_request=ListPageRequest(limit=1, offset=0),
        filters=TicketListFilters(is_backlogged=True, worker_types=("coding",)),
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
    )
    assert page.match_count == 1
    assert page.rows[0]["id"] == deferred and page.rows[0]["is_backlogged"] is True
    assert data.read_ticket(tmp_db, active).sprint_id is None
    assert data.read_ticket(tmp_db, active).is_backlogged is False


def test_retirement_refuses_unresolved_manager_delivery_atomically(tmp_db: Connection) -> None:
    tid = create(tmp_db)
    tmp_db.execute(
        "INSERT INTO manager_wake_batches(sender_message_id,message,status,created_at,updated_at,"
        "target_kind,target_id) VALUES ('m_retire','Pending notice','uncertain',10,10,'ticket',?)",
        (tid,),
    )
    tmp_db.commit()
    before = data.ticket_record_sha256(tmp_db, tid)
    with pytest.raises(PlannerError, match="ownership"):
        retire(tmp_db, tid)
    assert data.ticket_record_sha256(tmp_db, tid) == before
    tmp_db.execute("UPDATE manager_wake_batches SET status='refused'")
    tmp_db.commit()
    assert retire(tmp_db, tid).stage == "cancelled"


def test_retirement_preserves_and_clears_revision_feedback(tmp_db: Connection) -> None:
    from planner.tickets import revision_feedback

    tid = create(tmp_db)
    revision_feedback.set_feedback(
        tmp_db, tid, stage="needs_brief", sender=OWNER_PRINCIPAL, message="Rework first", now=11
    )
    tmp_db.commit()
    retired = retire(tmp_db, tid)
    provenance = json.loads(retired.field_values["retirement"])
    assert "Rework first" in provenance["original_revision_feedback"]["feedback_json"]
    assert (
        tmp_db.execute(
            "SELECT 1 FROM ticket_revision_feedback WHERE ticket_id=?", (tid,)
        ).fetchone()
        is None
    )


def test_backlog_does_not_release_occupied_closeout_lane(tmp_db: Connection) -> None:
    occupied = create(tmp_db)
    waiting = create(tmp_db)
    tmp_db.execute(
        "UPDATE tickets SET stage='needs_consequences', ceiling='needs_consequences', "
        "pending_proposal=NULL,worker_step_claim='out' WHERE id=?",
        (occupied,),
    )
    tmp_db.execute(
        "UPDATE tickets SET stage='needs_consequences', ceiling='needs_consequences', "
        "pending_proposal=NULL WHERE id=?",
        (waiting,),
    )
    tmp_db.commit()
    backlog(tmp_db, occupied)
    definition = configured_worker_type_registry().require("coding")
    assert not is_ready_for_worker_step(
        tmp_db, data.read_ticket(tmp_db, waiting), worker_type_definition=definition
    )


def ready_ticket(conn: Connection, *, ceiling: str = "needs_success_condition") -> Ticket:
    tid = create(conn)
    return data.accept_proposal(
        conn,
        tid,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=11,
        next_ceiling=ceiling,
        next_holder=OWNER_PRINCIPAL,
    )


def test_backlog_after_ready_scan_refuses_transactional_claim(tmp_db: Connection) -> None:
    from planner.core.db import connect

    ticket = ready_ticket(tmp_db)
    definition = configured_worker_type_registry().require("coding")
    # The loop's earlier snapshot is ready. Another request defers it before claim.
    assert is_ready_for_worker_step(tmp_db, ticket, worker_type_definition=definition)
    path = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    peer = connect(path)
    try:
        backlog(peer, ticket.id)
        before_claim = data.read_ticket(tmp_db, ticket.id)
        assert (
            data.claim_ticket_for_worker_step(
                tmp_db,
                ticket.id,
                readiness_check=is_ready_for_worker_step,
                now=21,
            )
            is None
        )
        after_claim = data.read_ticket(tmp_db, ticket.id)
        assert after_claim.worker_step_claim is WorkerStepClaim.none
        assert after_claim.worker_step_claim_revision == before_claim.worker_step_claim_revision
        assert after_claim.stage == ticket.stage and after_claim.ceiling == ticket.ceiling
    finally:
        peer.close()


def test_resume_preserves_pending_approval_and_live_blocker(tmp_db: Connection) -> None:
    from planner.tickets.actions import add_ticket_block

    pending = create(tmp_db)
    original = data.read_ticket(tmp_db, pending)
    backlog(tmp_db, pending)
    resumed_pending = backlog(tmp_db, pending, False)
    assert resumed_pending.stage == original.stage
    assert resumed_pending.ceiling == original.ceiling
    assert resumed_pending.pending_proposal == original.pending_proposal
    assert (
        data.claim_ticket_for_worker_step(
            tmp_db,
            pending,
            readiness_check=is_ready_for_worker_step,
            now=21,
        )
        is None
    )

    blocked = ready_ticket(tmp_db)
    blocker = create(tmp_db)
    add_ticket_block(tmp_db, blocker, blocked.id, now=12)
    backlog(tmp_db, blocked.id)
    resumed_blocked = backlog(tmp_db, blocked.id, False)
    assert resumed_blocked.stage == blocked.stage and resumed_blocked.ceiling == blocked.ceiling
    assert resumed_blocked.pending_proposal is None
    assert (
        data.claim_ticket_for_worker_step(
            tmp_db,
            blocked.id,
            readiness_check=is_ready_for_worker_step,
            now=21,
        )
        is None
    )
    assert (
        tmp_db.execute(
            "SELECT blocking_ticket_id FROM ticket_blocks WHERE blocked_ticket_id=?",
            (blocked.id,),
        ).fetchone()[0]
        == blocker
    )


def test_resume_keeps_ceiling_and_parks_new_proposal_at_its_gate(tmp_db: Connection) -> None:
    ticket = ready_ticket(tmp_db)
    assert ticket.stage == ticket.ceiling == "needs_success_condition"
    backlog(tmp_db, ticket.id)
    resumed = backlog(tmp_db, ticket.id, False)
    assert resumed.stage == ticket.stage and resumed.ceiling == ticket.ceiling
    assert resumed.pending_proposal is None
    # The ceiling parks the answer. It does not prevent the worker from filling it.
    claimed = data.claim_ticket_for_worker_step(
        tmp_db,
        ticket.id,
        readiness_check=is_ready_for_worker_step,
        now=21,
    )
    assert claimed is not None and claimed.stage == ticket.stage
    proposed = data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Observable result",
        principal=ticket_principal(ticket.id),
        now=22,
    )
    assert proposed.stage == ticket.stage and proposed.ceiling == ticket.ceiling
    assert proposed.pending_proposal is not None
    assert proposed.pending_proposal.field == "success_condition"
    assert proposed.pending_proposal.body == "Observable result"
    assert "success_condition" not in proposed.field_values


@pytest.mark.parametrize("resumed_wins", [False, True])
def test_closeout_resume_and_peer_claim_recheck_lane_from_fresh_state(
    tmp_db: Connection,
    resumed_wins: bool,
) -> None:
    from planner.core.db import connect

    deferred = create(tmp_db)
    peer_id = create(tmp_db)
    tmp_db.execute(
        "UPDATE tickets SET stage='needs_consequences',ceiling='needs_consequences',"
        "pending_proposal=NULL WHERE id IN (?,?)",
        (deferred, peer_id),
    )
    tmp_db.commit()
    backlog(tmp_db, deferred)
    path = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    peer = connect(path)
    try:
        definition = configured_worker_type_registry().require("coding")
        # A peer scans an empty lane before the deferred contender resumes.
        assert is_ready_for_worker_step(
            peer,
            data.read_ticket(peer, peer_id),
            worker_type_definition=definition,
        )
        backlog(tmp_db, deferred, False)
        assert is_ready_for_worker_step(
            tmp_db,
            data.read_ticket(tmp_db, deferred),
            worker_type_definition=definition,
        )
        first, second = (deferred, peer_id) if resumed_wins else (peer_id, deferred)
        first_conn, second_conn = (tmp_db, peer) if resumed_wins else (peer, tmp_db)
        holder = data.claim_ticket_for_worker_step(
            first_conn,
            first,
            readiness_check=is_ready_for_worker_step,
            now=21,
        )
        assert holder is not None and holder.worker_step_claim is WorkerStepClaim.out
        assert (
            data.claim_ticket_for_worker_step(
                second_conn,
                second,
                readiness_check=is_ready_for_worker_step,
                now=21,
            )
            is None
        )
        # Deferral of a delivered holder cannot transfer its lane or claim to its peer.
        backlog(first_conn, first)
        backlog(second_conn, second)
        backlog(second_conn, second, False)
        assert (
            data.claim_ticket_for_worker_step(
                second_conn,
                second,
                readiness_check=is_ready_for_worker_step,
                now=22,
            )
            is None
        )
        preserved = data.read_ticket(first_conn, first)
        assert preserved.worker_step_claim is holder.worker_step_claim
        assert preserved.worker_step_claim_revision == holder.worker_step_claim_revision
        assert preserved.stage == holder.stage and preserved.ceiling == holder.ceiling
        assert preserved.pending_proposal == holder.pending_proposal
    finally:
        peer.close()
