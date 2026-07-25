"""The complete Automatic Employee-step eligibility decision table."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.runtime.automatic_employee_step_eligibility import (
    is_eligible_for_automatic_employee_step,
)
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, StageOwnershipMode, Ticket, TicketStatus
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION

PLANNING_DAY_ID = "day_2026-07-14"
OTHER_DAY_ID = "day_2026-07-13"


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "eligibility.db"))
    create_schema(conn)
    return conn


def _ticket(
    conn: sqlite3.Connection,
    *,
    worker_type: str = "coding",
    planning_day_id: str | None = PLANNING_DAY_ID,
    ceiling: str | None = None,
    at_cap: AtCap = AtCap.propose,
) -> Ticket:
    definition = configured_worker_type_registry().require(worker_type)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type=worker_type,
        title=f"{worker_type} ticket",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    ticket = tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling=ceiling or definition.first_worker_stage(),
        at_cap=at_cap,
    )
    if planning_day_id is not None:
        days_data.add_day_ticket(conn, planning_day_id, ticket.id, 3)
    return ticket


def _eligible(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str = PLANNING_DAY_ID,
    definition: WorkerTypeDefinition | None = None,
) -> bool:
    return is_eligible_for_automatic_employee_step(
        conn,
        tickets_data.read_ticket(conn, ticket.id),
        planning_day_id=planning_day_id,
        worker_type_definition=(
            definition or configured_worker_type_registry().require(ticket.worker_type)
        ),
    )


def _worker_turn(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
    status: str = "complete",
) -> None:
    repository = SqliteEmployeeStepRepository()
    turn = repository.start(conn, ticket_id, now=now)
    if status != "running":
        repository.settle(
            conn,
            turn.employee_step_id,
            ticket_id=ticket_id,
            status=status,  # type: ignore[arg-type]
            error="failed" if status == "errored" else None,
            now=now + 1,
        )


def _block(
    conn: sqlite3.Connection, *, blocker_id: str, target_id: str, now: int
) -> None:
    """Block a Ticket the way the API does, so its status settles to `blocked`."""
    tickets_actions.add_link(
        conn,
        blocker_id,
        target_id,
        LinkKind.blocks,
        now=now,
    )


@pytest.mark.parametrize(
    ("worker_type", "first_stage", "expected_eligible", "definition"),
    [
        ("coding", "needs_success", True, CODING_WORKER_TYPE_DEFINITION),
        ("new_worker", "needs_understanding", True, NEW_WORKER_TYPE_DEFINITION),
    ],
)
def test_shipped_worker_types_use_their_real_first_employee_stage_ownership(
    tmp_path: Path,
    worker_type: str,
    first_stage: str,
    expected_eligible: bool,
    definition: WorkerTypeDefinition,
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type=worker_type)
        assert ticket.stage == first_stage
        assert _eligible(conn, ticket, definition=definition) is expected_eligible
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("ownership_mode", "expected"),
    [
        (StageOwnershipMode.worker, True),
        (StageOwnershipMode.user, False),
        (StageOwnershipMode.paired, True),
    ],
)
def test_effective_stage_ownership_controls_automatic_eligibility(
    tmp_path: Path,
    ownership_mode: StageOwnershipMode,
    expected: bool,
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)
        tickets_data.set_stage_ownership(
            conn,
            ticket.id,
            stage=ticket.stage,
            ownership_mode=ownership_mode,
            now=4,
        )
        # Hold every other eligibility conjunct constant so this pins ownership itself.
        conn.execute(
            "UPDATE tickets SET ticket_status = 'empty' WHERE id = ?",
            (ticket.id,),
        )
        assert _eligible(conn, ticket) is expected
    finally:
        conn.close()


def test_membership_must_match_the_explicit_planning_day(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        no_day = _ticket(conn, planning_day_id=None)
        other_day = _ticket(conn, planning_day_id=OTHER_DAY_ID)
        supplied_day = _ticket(conn)

        assert not _eligible(conn, no_day)
        assert not _eligible(conn, other_day)
        assert _eligible(conn, supplied_day)
        assert not _eligible(conn, supplied_day, planning_day_id=OTHER_DAY_ID)
    finally:
        conn.close()


def test_paired_owned_ticket_resting_at_paired_is_never_startable(tmp_path: Path) -> None:
    # The event-log marker scan is gone: `paired` is a control status like any other,
    # so a paired-owned Ticket resting at `paired` is never loop-startable. Only the
    # same Ticket at `empty` is.
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type="new_worker")
        tickets_data.set_stage_ownership(
            conn,
            ticket.id,
            stage=ticket.stage,
            ownership_mode=StageOwnershipMode.paired,
            now=4,
        )
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (TicketStatus.paired.value, ticket.id),
        )
        assert not _eligible(conn, ticket, definition=NEW_WORKER_TYPE_DEFINITION)

        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (TicketStatus.empty.value, ticket.id),
        )
        assert _eligible(conn, ticket, definition=NEW_WORKER_TYPE_DEFINITION)
    finally:
        conn.close()


def test_eligibility_source_keeps_no_event_log_machinery() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "src/planner/runtime/automatic_employee_step_eligibility.py"
    ).read_text(encoding="utf-8")
    for deleted in (
        "_latest_current_paired_stage_marker_event",
        "_has_worker_step_started_in_event_range",
        "_paired_status_allows_automatic_opening",
        "EventKind",
        "FROM events",
        "is_blocked",
    ):
        assert deleted not in source, deleted


@pytest.mark.parametrize(
    "break_one_conjunct",
    [
        "membership",
        "active_employee_step",
        "terminal",
        "next_gate",
        "proposal",
        "scope",
        "blocker",
    ],
)
def test_paired_stage_preserves_every_non_ownership_eligibility_factor(
    tmp_path: Path, break_one_conjunct: str
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(
            conn,
            worker_type="new_worker",
            ceiling="needs_understanding",
            at_cap=AtCap.propose,
        )
        assert _eligible(conn, ticket)

        definition: WorkerTypeDefinition | Any = NEW_WORKER_TYPE_DEFINITION
        if break_one_conjunct == "membership":
            days_data.remove_day_ticket(conn, PLANNING_DAY_ID, ticket.id, 5)
        elif break_one_conjunct == "active_employee_step":
            SqliteEmployeeStepRepository().start(conn, ticket.id, now=5)
        elif break_one_conjunct == "terminal":
            conn.execute("UPDATE tickets SET stage = 'done' WHERE id = ?", (ticket.id,))
        elif break_one_conjunct == "next_gate":

            class NoNextGate:
                def is_terminal(self, _stage: str) -> bool:
                    return False

                def gating_field(self, _stage: str) -> None:
                    return None

                def stage_definition(self, _stage: str) -> object:
                    return type(
                        "Stage",
                        (),
                        {"default_ownership_mode": StageOwnershipMode.paired},
                    )()

            definition = NoNextGate()
        elif break_one_conjunct == "proposal":
            tickets_data.file_proposal(
                conn,
                ticket.id,
                field="understanding",
                body="parked",
                actor="agent",
                now=5,
            )
        elif break_one_conjunct == "scope":
            tickets_data.change_scope(
                conn,
                ticket.id,
                ceiling="needs_understanding",
                at_cap=AtCap.stop,
                actor="human",
                now=5,
            )
        else:
            blocker = _ticket(conn, planning_day_id=None)
            _block(conn, blocker_id=blocker.id, target_id=ticket.id, now=5)

        assert not _eligible(conn, ticket, definition=definition)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "ticket_status",
    [
        TicketStatus.agent,
        TicketStatus.awaiting_approval,
        TicketStatus.paired,
        TicketStatus.user,
        TicketStatus.errored,
    ],
)
def test_every_non_empty_control_status_is_ineligible(
    tmp_path: Path, ticket_status: TicketStatus
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (ticket_status.value, ticket.id),
        )
        assert not _eligible(conn, ticket)
    finally:
        conn.close()


# Under the default worker-owned first stage, only `empty` is auto-runnable; every other
# control status is a deliberate "not eligible" decision. This partitions the full
# TicketStatus set so a future status cannot silently become auto-runnable.
_AUTO_ELIGIBLE_UNDER_WORKER_OWNERSHIP: dict[TicketStatus, bool] = {
    TicketStatus.empty: True,
    TicketStatus.blocked: False,
    TicketStatus.agent: False,
    TicketStatus.awaiting_approval: False,
    TicketStatus.paired: False,
    TicketStatus.user: False,
    TicketStatus.needs_user: False,
    TicketStatus.errored: False,
}


def test_every_ticket_status_has_an_explicit_auto_eligibility_decision(tmp_path: Path) -> None:
    assert set(_AUTO_ELIGIBLE_UNDER_WORKER_OWNERSHIP) == set(TicketStatus)
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)
        for status, expected in _AUTO_ELIGIBLE_UNDER_WORKER_OWNERSHIP.items():
            conn.execute(
                "UPDATE tickets SET ticket_status = ? WHERE id = ?",
                (status.value, ticket.id),
            )
            assert _eligible(conn, ticket) is expected, status
    finally:
        conn.close()


@pytest.mark.parametrize("terminal_stage", ["done", "dropped"])
def test_terminal_stages_are_ineligible(tmp_path: Path, terminal_stage: str) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)
        conn.execute("UPDATE tickets SET stage = ? WHERE id = ?", (terminal_stage, ticket.id))
        assert not _eligible(conn, ticket)
    finally:
        conn.close()


def test_non_terminal_stage_without_a_gated_field_is_ineligible(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)

        class DefinitionWithoutNextGate:
            def is_terminal(self, stage: str) -> bool:
                assert stage == ticket.stage
                return False

            def gating_field(self, stage: str) -> None:
                assert stage == ticket.stage
                return None

            def stage_definition(self, stage: str) -> object:
                assert stage == ticket.stage
                return type(
                    "Stage",
                    (),
                    {"default_ownership_mode": StageOwnershipMode.worker},
                )()

        assert not _eligible(
            conn,
            ticket,
            definition=DefinitionWithoutNextGate(),  # type: ignore[arg-type]
        )
    finally:
        conn.close()


def test_parked_current_field_proposal_is_ineligible(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)
        ticket = tickets_data.file_proposal(
            conn,
            ticket.id,
            field="success",
            body="parked",
            actor="agent",
            now=4,
        )
        assert ticket.ticket_status is TicketStatus.awaiting_approval
        assert not _eligible(conn, ticket)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("worker_type", "ceiling", "at_cap", "expected"),
    [
        ("coding", "needs_approach", AtCap.stop, True),
        ("coding", "needs_success", AtCap.propose, True),
        ("coding", "needs_success", AtCap.stop, False),
        ("new_worker", "needs_thinking", AtCap.stop, True),
        ("new_worker", "needs_stages", AtCap.propose, True),
        ("new_worker", "needs_stages", AtCap.stop, False),
    ],
)
def test_scope_permission_uses_the_ticket_worker_type_definition(
    tmp_path: Path,
    worker_type: str,
    ceiling: str,
    at_cap: AtCap,
    expected: bool,
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type=worker_type, ceiling=ceiling, at_cap=at_cap)
        if worker_type == "new_worker":
            tickets_data.file_proposal(
                conn,
                ticket.id,
                field="understanding",
                body="understanding",
                actor="agent",
                now=3,
            )
            ticket = tickets_data.accept_proposal(
                conn,
                ticket.id,
                field="understanding",
                actor="human",
                now=4,
                next_ceiling=ceiling,
                at_cap=at_cap,
            )
            assert ticket.stage == "needs_stages"
        assert _eligible(conn, ticket) is expected
    finally:
        conn.close()


@pytest.mark.parametrize("settled_stage", ["done", "dropped"])
def test_a_completing_blocker_frees_its_target_for_automatic_work(
    tmp_path: Path, settled_stage: str
) -> None:
    # Eligibility itself no longer looks at links: the completing blocker rewrites the
    # target's status back to empty, and that is what makes it startable again.
    conn = _db(tmp_path)
    try:
        blocker = _ticket(conn, planning_day_id=None)
        target = _ticket(conn)
        _block(conn, blocker_id=blocker.id, target_id=target.id, now=4)
        assert tickets_data.read_ticket(conn, target.id).ticket_status is TicketStatus.blocked
        assert not _eligible(conn, target)

        if settled_stage == "done":
            tickets_data.set_stage(conn, blocker.id, new_stage="done", actor="human", now=5)
        else:
            tickets_data.drop_ticket(conn, blocker.id, actor="human", now=5)

        assert tickets_data.read_ticket(conn, target.id).ticket_status is TicketStatus.empty
        assert _eligible(conn, target)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("turn_status", "expected"),
    [
        ("running", False),
        ("complete", True),
        ("errored", True),
        ("interrupted", True),
    ],
)
def test_only_a_running_employee_step_blocks_automatic_employee_step(
    tmp_path: Path,
    turn_status: str,
    expected: bool,
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn)
        repository = SqliteEmployeeStepRepository()
        turn = repository.start(conn, ticket.id, now=4)
        if turn_status != "running":
            repository.settle(
                conn,
                turn.employee_step_id,
                ticket_id=ticket.id,
                status=turn_status,  # type: ignore[arg-type]
                error="failed" if turn_status == "errored" else None,
                now=5,
            )

        assert _eligible(conn, ticket) is expected
    finally:
        conn.close()


@pytest.mark.parametrize(
    "break_one_conjunct",
    [
        "membership",
        "status",
        "terminal",
        "next_gate",
        "proposal",
        "scope",
        "blocker",
        "active_employee_step",
    ],
)
def test_all_conjuncts_true_then_one_factor_at_a_time_false(
    tmp_path: Path, break_one_conjunct: str
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, ceiling="needs_success", at_cap=AtCap.propose)
        assert _eligible(conn, ticket)

        definition: WorkerTypeDefinition | Any = CODING_WORKER_TYPE_DEFINITION
        if break_one_conjunct == "membership":
            days_data.remove_day_ticket(conn, PLANNING_DAY_ID, ticket.id, 5)
        elif break_one_conjunct == "status":
            conn.execute(
                "UPDATE tickets SET ticket_status = 'user' WHERE id = ?",
                (ticket.id,),
            )
        elif break_one_conjunct == "terminal":
            conn.execute("UPDATE tickets SET stage = 'done' WHERE id = ?", (ticket.id,))
        elif break_one_conjunct == "next_gate":

            class NoNextGate:
                def is_terminal(self, _stage: str) -> bool:
                    return False

                def gating_field(self, _stage: str) -> None:
                    return None

                def stage_definition(self, _stage: str) -> object:
                    return type(
                        "Stage",
                        (),
                        {"default_ownership_mode": StageOwnershipMode.worker},
                    )()

            definition = NoNextGate()
        elif break_one_conjunct == "proposal":
            tickets_data.file_proposal(
                conn,
                ticket.id,
                field="success",
                body="parked",
                actor="agent",
                now=5,
            )
        elif break_one_conjunct == "scope":
            tickets_data.change_scope(
                conn,
                ticket.id,
                ceiling="needs_success",
                at_cap=AtCap.stop,
                actor="human",
                now=5,
            )
        elif break_one_conjunct == "blocker":
            blocker = _ticket(conn, planning_day_id=None)
            _block(conn, blocker_id=blocker.id, target_id=ticket.id, now=5)
        else:
            SqliteEmployeeStepRepository().start(conn, ticket.id, now=5)

        assert not _eligible(conn, ticket, definition=definition)
    finally:
        conn.close()


def test_new_worker_novel_stage_is_never_interpreted_as_coding(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type="new_worker")
        assert ticket.stage == "needs_understanding"
        assert _eligible(conn, ticket, definition=NEW_WORKER_TYPE_DEFINITION)
        conn.execute("UPDATE tickets SET ticket_status = 'empty' WHERE id = ?", (ticket.id,))
        with pytest.raises(Exception, match="stage outside the linear order"):
            _eligible(conn, ticket, definition=CODING_WORKER_TYPE_DEFINITION)
    finally:
        conn.close()


def test_deleted_runtime_and_test_paths_stay_deleted() -> None:
    root = Path(__file__).resolve().parents[2]
    deleted = (
        "src/planner/runtime/readiness.py",
        "src/planner/runtime/ticket_readiness_loop.py",
        "src/planner/runtime/readiness_doorbell.py",
        "tests/unit/test_ticket_readiness_loop.py",
        "tests/unit/test_readiness_doorbell.py",
        "tests/unit/test_readiness_actions.py",
    )
    assert not [path for path in deleted if (root / path).exists()]


def test_claim_writer_has_only_the_required_complete_eligibility_seam() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "src/planner/tickets/data.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "claim_automatic_employee_step"
    )
    keyword_names = [argument.arg for argument in function.args.kwonlyargs]
    defaults = dict(zip(keyword_names, function.args.kw_defaults, strict=True))
    assert keyword_names == ["planning_day_id_resolver", "eligibility_check", "now"]
    assert defaults["planning_day_id_resolver"] is None
    assert defaults["eligibility_check"] is None
    source = ast.unparse(function)
    assert "guard" not in source
    assert "ticket.ticket_status" not in source
    assert "eligibility_check" in source
    assert "planning_day_id=planning_day_id" in source
    assert "worker_type_definition=worker_type_definition" in source
    assert "chat_turns" not in source

    eligibility_path = root / "src/planner/runtime/automatic_employee_step_eligibility.py"
    eligibility_source = eligibility_path.read_text(encoding="utf-8")
    assert "SqliteEmployeeStepRepository" in eligibility_source
    assert ".running_exists(" in eligibility_source
