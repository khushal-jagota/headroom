"""The only module that writes Ticket rows. Stage, ceiling/at_cap, and fields
value mutations happen in exactly one function (_apply_decision); every public
writer is one BEGIN IMMEDIATE transaction. An ordinary Ticket edit validates and
writes its requested plain attributes together. Other semantic writers remain
separate. sqlite3 and ids live here only; the clock arrives as now (unix
seconds) and the title limit as an argument."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

from planner.conversation.contracts import require_conversation_backend_key
from planner.core import ticket_blocks
from planner.core.contracts import (
    Principal,
    PrincipalKind,
    Priority,
    principal_legacy_actor,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.days import data as days_data
from planner.notifications.attention import capture_ticket_attention
from planner.tickets import revision_feedback
from planner.tickets.contracts import (
    AtCap,
    EmployeeLaunchConfiguration,
    NextCeiling,
    PendingTicketProposal,
    ProjectPriorityAnchor,
    ResolvedTicketPriorityAnchors,
    SprintItemPriorityAnchor,
    StageOwnershipMode,
    Ticket,
    TicketDeletion,
    TicketEdit,
    TicketFieldValues,
    TicketStatus,
)
from planner.tickets.logic import (
    admission,
    employee_configuration,
    fields_codec,
    machine,
    resolution,
)
from planner.tickets.logic.decisions import Decision
from planner.worker_settings.service import (
    read_worker_launch_defaults_for_ticket_creation,
)
from planner.worker_types.configuration import (
    ConfiguredWorkerRuntimeDefinitions,
    configured_worker_runtime_definitions,
    configured_worker_type_registry,
)
from planner.worker_types.contracts import WorkerTypeDefinition


class _WorkerStepReadinessCheck(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        ticket: Ticket,
        *,
        planning_day_id: str,
        worker_type_definition: WorkerTypeDefinition,
    ) -> bool: ...


def _principal_to_json(principal: Principal) -> str:
    return json.dumps(
        {"kind": principal.kind.value, "id": principal.id},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _principal_from_json(raw: str) -> Principal:
    stored = json.loads(raw)
    if not isinstance(stored, dict):
        raise ValueError("stored principal must be an object")
    return Principal(PrincipalKind(str(stored["kind"])), str(stored["id"]))


def _validate_ceiling_holder(
    conn: sqlite3.Connection, holder: Principal, *, ticket_id: str
) -> None:
    if holder == Principal(PrincipalKind.ticket, ticket_id):
        raise PlannerError(
            ErrorCode.validation,
            "a Ticket cannot hold its own ceiling",
            {"ticket_id": ticket_id},
        )
    table = {
        PrincipalKind.ticket: "tickets",
        PrincipalKind.sprint_item: "sprint_items",
    }.get(holder.kind)
    if table is None:
        return
    row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (holder.id,)).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.validation,
            "ceiling holder does not exist",
            {"holder": {"kind": holder.kind.value, "id": holder.id}},
        )


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT ticket_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO ticket_write")
            conn.execute("RELEASE ticket_write")
            raise
        else:
            conn.execute("RELEASE ticket_write")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _validate_ticket_creation_placement(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item_id: str | None,
    blocked_by_ticket_ids: list[str] | None,
) -> None:
    if sprint_item_id is not None:
        if (
            conn.execute("SELECT 1 FROM sprint_items WHERE id = ?", (sprint_item_id,)).fetchone()
            is None
        ):
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        item = conn.execute(
            "SELECT project_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is None:
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        if project_id != item["project_id"]:
            raise PlannerError(ErrorCode.validation, "ticket placement does not match sprint item")
    if project_id is not None:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
    if (
        sprint_id is not None
        and conn.execute("SELECT 1 FROM sprints WHERE id = ?", (sprint_id,)).fetchone() is None
    ):
        raise PlannerError(ErrorCode.validation, "invalid sprint_id", {"sprint_id": sprint_id})
    for blocker_ticket_id in blocked_by_ticket_ids or []:
        if (
            conn.execute("SELECT 1 FROM tickets WHERE id = ?", (blocker_ticket_id,)).fetchone()
            is None
        ):
            raise PlannerError(
                ErrorCode.ticket_block_invalid,
                "blocking_ticket_id must be an existing ticket",
                {"blocking_ticket_id": blocker_ticket_id},
            )


def _resolve_priority_anchors(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    sprint_item_id: str | None,
) -> ResolvedTicketPriorityAnchors:
    """Load the placement anchors that explain a new Ticket's priority default."""
    if sprint_item_id is not None:
        row = conn.execute(
            "SELECT sprint_items.id AS sprint_item_id, "
            "sprint_items.title AS sprint_item_title, "
            "sprint_items.priority AS sprint_item_priority, "
            "projects.id AS project_id, projects.name AS project_name, "
            "projects.priority AS project_priority "
            "FROM sprint_items JOIN projects ON projects.id = sprint_items.project_id "
            "WHERE sprint_items.id = ?",
            (sprint_item_id,),
        ).fetchone()
        if row is None:
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        return ResolvedTicketPriorityAnchors(
            sprint_item=SprintItemPriorityAnchor(
                id=str(row["sprint_item_id"]),
                title=str(row["sprint_item_title"]),
                priority=Priority(str(row["sprint_item_priority"])),
            ),
            project=ProjectPriorityAnchor(
                id=str(row["project_id"]),
                name=str(row["project_name"]),
                priority=(
                    Priority(str(row["project_priority"]))
                    if row["project_priority"] is not None
                    else None
                ),
            ),
        )
    if project_id is not None:
        row = conn.execute(
            "SELECT id, name, priority FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
        return ResolvedTicketPriorityAnchors(
            sprint_item=None,
            project=ProjectPriorityAnchor(
                id=str(row["id"]),
                name=str(row["name"]),
                priority=(Priority(str(row["priority"])) if row["priority"] is not None else None),
            ),
        )
    return ResolvedTicketPriorityAnchors(sprint_item=None, project=None)


def _created_ticket_priority(
    explicit_priority: Priority | None,
    anchors: ResolvedTicketPriorityAnchors,
) -> Priority:
    if explicit_priority is not None:
        return explicit_priority
    if anchors.sprint_item is not None:
        return anchors.sprint_item.priority
    if anchors.project is not None and anchors.project.priority is not None:
        return anchors.project.priority
    return Priority.P3


def validate_ticket_creation_context(
    conn: sqlite3.Connection,
    *,
    title: str,
    title_max_chars: int,
    worker_type: str,
    employee_backend: str | None = None,
    employee_launch_model: str | None = None,
    project_id: str | None = None,
    sprint_id: str | None = None,
    deadline: str | None = None,
    sprint_item_id: str | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    worker_runtime_definitions: ConfiguredWorkerRuntimeDefinitions | None = None,
) -> None:
    """Validate a future ordinary Ticket without creating one."""
    admission.validate_title(title, title_max_chars)
    admission.validate_deadline(deadline)
    runtime_definitions = worker_runtime_definitions or configured_worker_runtime_definitions()
    runtime_definitions.worker_type_registry.require(worker_type)
    launch_defaults = read_worker_launch_defaults_for_ticket_creation(
        conn, runtime_definitions.worker_type_registry, worker_type
    )
    employee_configuration.launch_configuration_for_a_new_ticket(
        default_backend=launch_defaults.employee_backend,
        default_model=launch_defaults.employee_launch_model,
        default_reasoning_effort=launch_defaults.employee_launch_reasoning_effort,
        employee_backend=require_conversation_backend_key(
            employee_backend if employee_backend is not None else launch_defaults.employee_backend
        ),
        employee_launch_model=employee_launch_model,
    )
    if sprint_item_id is not None:
        item = conn.execute(
            "SELECT project_id FROM sprint_items WHERE id = ?",
            (sprint_item_id,),
        ).fetchone()
        if item is not None and project_id is None:
            project_id = str(item["project_id"])
    _validate_ticket_creation_placement(
        conn,
        project_id=project_id,
        sprint_id=sprint_id,
        sprint_item_id=sprint_item_id,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
    )


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    worker_type = str(row["worker_type"])
    worker_type_definition = configured_worker_type_registry().require(worker_type)
    stage = str(row["stage"])
    worker_type_definition.validate_ticket_position(stage, str(row["ceiling"]))
    return Ticket(
        id=row["id"],
        title=row["title"],
        stage=stage,
        priority=Priority(row["priority"]),
        deadline=row["deadline"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        sprint_id=row["sprint_id"],
        sprint_item_id=row["sprint_item_id"],
        effective_sprint_id=row["sprint_id"],
        resolved_priority_anchors=ResolvedTicketPriorityAnchors(
            sprint_item=(
                SprintItemPriorityAnchor(
                    id=str(row["sprint_item_id"]),
                    title=str(row["priority_sprint_item_title"]),
                    priority=Priority(str(row["priority_sprint_item_priority"])),
                )
                if row["sprint_item_id"] is not None
                else None
            ),
            project=(
                ProjectPriorityAnchor(
                    id=str(row["project_id"]),
                    name=str(row["project_name"]),
                    priority=(
                        Priority(str(row["priority_project_priority"]))
                        if row["priority_project_priority"] is not None
                        else None
                    ),
                )
                if row["project_id"] is not None
                else None
            ),
        ),
        recap=row["recap"],
        guidance=row["guidance"],
        ceiling=str(row["ceiling"]),
        ceiling_holder=_principal_from_json(str(row["ceiling_holder"])),
        at_cap=AtCap(row["at_cap"]),
        ticket_status=TicketStatus(row["ticket_status"]),
        ticket_status_changed_at=int(row["ticket_status_changed_at"]),
        ticket_status_revision=int(row["ticket_status_revision"]),
        conversation_id=row["conversation_id"],
        field_values=fields_codec.values_from_json(
            row["field_values"], worker_type_definition.field_ids()
        ),
        pending_proposal=fields_codec.proposal_from_json(row["pending_proposal"]),
        archived_field_content=str(row["archived_field_content"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        worker_type=worker_type,
        employee_backend=str(row["employee_backend"]),
        employee_launch_model=(
            str(row["employee_launch_model"]) if row["employee_launch_model"] is not None else None
        ),
        employee_launch_reasoning_effort=(
            str(row["employee_launch_reasoning_effort"])
            if row["employee_launch_reasoning_effort"] is not None
            else None
        ),
    )


def _ticket_row(conn: sqlite3.Connection, ticket_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT tickets.*, projects.name AS project_name, "
        "projects.priority AS priority_project_priority, "
        "sprint_items.title AS priority_sprint_item_title, "
        "sprint_items.priority AS priority_sprint_item_priority "
        "FROM tickets LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects ON projects.id = tickets.project_id "
        "WHERE tickets.id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
    return row


def _load_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    return _row_to_ticket(_ticket_row(conn, ticket_id))


def _load_ticket_and_worker_type_definition_for_write(
    conn: sqlite3.Connection, ticket_id: str
) -> tuple[Ticket, WorkerTypeDefinition]:
    row = _ticket_row(conn, ticket_id)
    worker_type_definition = configured_worker_type_registry().require(str(row["worker_type"]))
    worker_type_definition.validate_ticket_position(str(row["stage"]), str(row["ceiling"]))
    ticket = _row_to_ticket(row)
    _validate_ceiling_holder(conn, ticket.ceiling_holder, ticket_id=ticket.id)
    fields_codec.validate_state(
        ticket.field_values,
        ticket.pending_proposal,
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    return ticket, worker_type_definition


def _load_ticket_for_write(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    ticket, _worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
        conn, ticket_id
    )
    return ticket


def _seed_kickoff(
    kickoff_note: str | None,
    principal: Principal,
    now: int,
    *,
    stage: str,
    ceiling: str,
    worker_type_definition: WorkerTypeDefinition,
) -> tuple[str, TicketFieldValues, PendingTicketProposal | None, TicketStatus]:
    ownership = machine.stage_ownership_mode(
        stage,
        worker_type_definition=worker_type_definition,
    )
    if ownership is None:
        raise PlannerError(ErrorCode.validation, "kickoff stage cannot be terminal")
    if not worker_type_definition.has_field("kickoff"):
        return stage, {}, None, machine.resting_ticket_status(ownership)
    if kickoff_note is None:
        return stage, {}, None, TicketStatus.empty
    target = (
        machine.auto_accept_target(
            stage, ceiling, "kickoff", worker_type_definition=worker_type_definition
        )
        if ownership is StageOwnershipMode.worker
        else None
    )
    if target is not None:
        return target, {"kickoff": kickoff_note}, None, machine.resting_ticket_status(ownership)
    return (
        stage,
        {},
        PendingTicketProposal("kickoff", kickoff_note, principal_legacy_actor(principal), now),
        TicketStatus.awaiting_approval,
    )


def _require_current_supervisor_parent(
    conn: sqlite3.Connection,
    ticket: Ticket,
    sprint_item_id: str | None,
    principal: Principal,
    action: str = "Ticket review",
) -> None:
    """Recheck exact current parent while the resolving write holds its transaction."""
    if principal.kind is not PrincipalKind.sprint_item and sprint_item_id is None:
        return
    if principal.kind is not PrincipalKind.sprint_item or sprint_item_id != principal.id:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is not available to this Sprint Item supervisor",
            {
                "actor": principal_legacy_actor(principal),
                "sprint_item_id": sprint_item_id,
                "ticket_id": ticket.id,
            },
        )
    row = conn.execute(
        "SELECT 1 FROM sprint_items AS item "
        "WHERE item.id = ? AND item.kind = 'normal' AND ? = item.id",
        (sprint_item_id, ticket.sprint_item_id),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is not available to this Sprint Item supervisor",
            {
                "actor": principal_legacy_actor(principal),
                "sprint_item_id": sprint_item_id,
                "ticket_id": ticket.id,
            },
        )


def _active_blocker_stage(stage: str) -> bool:
    return stage not in {"done", "dropped"}


def _blocked_ticket_ids(conn: sqlite3.Connection, ticket_id: str) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT blocked_ticket_id FROM ticket_blocks "
        "WHERE blocking_ticket_id = ? ORDER BY blocked_ticket_id",
        (ticket_id,),
    ).fetchall()
    return tuple(str(row["blocked_ticket_id"]) for row in rows)


def _has_live_blocker(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """Return whether an active Ticket blocks this Ticket."""
    return (
        conn.execute(
            "SELECT 1 FROM ticket_blocks "
            "JOIN tickets blocker ON blocker.id = ticket_blocks.blocking_ticket_id "
            "WHERE ticket_blocks.blocked_ticket_id = ? "
            "AND blocker.stage NOT IN ('done', 'dropped') LIMIT 1",
            (ticket_id,),
        ).fetchone()
        is not None
    )


def _blocked_standin(
    conn: sqlite3.Connection, ticket_id: str, ticket_status: TicketStatus
) -> TicketStatus:
    """`blocked` stands in for `empty` while a live blocker exists; every other value
    passes through untouched. There is no condition on the Ticket's own stage."""
    if ticket_status is not TicketStatus.empty:
        return ticket_status
    if _has_live_blocker(conn, ticket_id):
        return TicketStatus.blocked
    return TicketStatus.empty


def settle_blocked_standin(conn: sqlite3.Connection, ticket_id: str, now: int) -> None:
    """Re-derive one resting Ticket's empty/blocked stand-in after its blockers changed.

    The single transition writer for the blocker-driven pair. A no-op unless the Ticket is
    currently resting at `empty` or `blocked` — every other status owns itself — and it
    writes only when the value actually changes, so a Ticket that stays blocked because
    another live blocker remains writes nothing.
    """
    row = conn.execute("SELECT ticket_status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        return
    current = TicketStatus(str(row["ticket_status"]))
    if current not in (TicketStatus.empty, TicketStatus.blocked):
        return
    target = _blocked_standin(conn, ticket_id, TicketStatus.empty)
    if target is current:
        return
    _write_ticket_status(conn, ticket_id, target, now)


def settle_blocked_standin_for_ticket(
    conn: sqlite3.Connection, blocked_ticket_id: str, now: int
) -> None:
    settle_blocked_standin(conn, blocked_ticket_id, now)


def _release_ticket_blocks(
    conn: sqlite3.Connection, ticket_id: str, blocked_ticket_ids: tuple[str, ...], now: int
) -> None:
    """Remove a completed Ticket's blocks and settle each blocked Ticket."""
    for blocked_ticket_id in blocked_ticket_ids:
        conn.execute(
            "DELETE FROM ticket_blocks "
            "WHERE blocking_ticket_id = ? AND blocked_ticket_id = ?",
            (ticket_id, blocked_ticket_id),
        )
    for blocked_ticket_id in blocked_ticket_ids:
        settle_blocked_standin_for_ticket(conn, blocked_ticket_id, now)


def _apply_decision(
    conn: sqlite3.Connection, ticket: Ticket, decision: Decision, now: int
) -> Ticket:
    new_stage = decision.stage
    new_ceiling = decision.ceiling
    new_at_cap = decision.at_cap
    _validate_ceiling_holder(conn, decision.ceiling_holder, ticket_id=ticket.id)
    # Pre-persist door: the prospective (stage, ceiling) must be registry-valid for
    # this ticket's type before any SQL — the enforcement the dropped DB CHECKs gave.
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)
    worker_type_definition.validate_ticket_position(str(new_stage), str(new_ceiling))
    fields_codec.validate_state(
        decision.field_values,
        decision.pending_proposal,
        new_stage,
        worker_type_definition=worker_type_definition,
    )
    active_before = _active_blocker_stage(ticket.stage)
    active_after = _active_blocker_stage(new_stage)
    affected_blocked_ticket_ids: tuple[str, ...] = ()
    if active_before != active_after:
        affected_blocked_ticket_ids = _blocked_ticket_ids(conn, ticket.id)
        if active_after:
            for blocked_ticket_id in affected_blocked_ticket_ids:
                if ticket_blocks.would_create_active_ticket_block_cycle(
                    conn, ticket.id, blocked_ticket_id
                ):
                    raise PlannerError(
                        ErrorCode.ticket_block_cycle,
                        "ticket stage change would activate a Ticket block cycle",
                        {
                            "blocking_ticket_id": ticket.id,
                            "blocked_ticket_id": blocked_ticket_id,
                        },
                    )
    conn.execute(
        "UPDATE tickets SET field_values = ?, pending_proposal = ?, archived_field_content = ?, "
        "stage = ?, ceiling = ?, ceiling_holder = ?, at_cap = ?, updated_at = ? WHERE id = ?",
        (
            fields_codec.values_to_json(decision.field_values),
            fields_codec.proposal_to_json(decision.pending_proposal),
            decision.archived_field_content,
            str(new_stage),
            str(new_ceiling),
            _principal_to_json(decision.ceiling_holder),
            new_at_cap.value,
            now,
            ticket.id,
        ),
    )
    if str(new_stage) != ticket.stage:
        conn.execute(
            "DELETE FROM ticket_paired_stage_openers WHERE ticket_id = ?",
            (ticket.id,),
        )
        revision_feedback.discard(conn, ticket.id)
    if active_before != active_after:
        if active_after:
            for blocked_ticket_id in affected_blocked_ticket_ids:
                settle_blocked_standin_for_ticket(conn, blocked_ticket_id, now)
        else:
            _release_ticket_blocks(conn, ticket.id, affected_blocked_ticket_ids, now)
    capture_ticket_attention(conn, ticket.id, now)
    return _load_ticket(conn, ticket.id)


def _write_ticket_status(
    conn: sqlite3.Connection,
    ticket_id: str,
    ticket_status: TicketStatus,
    now: int,
) -> None:
    # The one write door also carries the stand-in: a caller asking for `empty` on a
    # Ticket with a live blocker durably lands on `blocked`.
    ticket_status = _blocked_standin(conn, ticket_id, ticket_status)
    # ticket_status_changed_at answers "how long has this Ticket been where it is",
    # so it moves only when the value really moves — rewriting the same status is not a
    # change. The CASE keeps that comparison against the stored row, in the one write.
    conn.execute(
        "UPDATE tickets SET ticket_status = ?, updated_at = ?, "
        "ticket_status_changed_at = CASE WHEN ticket_status = ? "
        "THEN ticket_status_changed_at ELSE ? END, "
        "ticket_status_revision = CASE WHEN ticket_status = ? "
        "THEN ticket_status_revision ELSE ticket_status_revision + 1 END WHERE id = ?",
        (
            ticket_status.value,
            now,
            ticket_status.value,
            now,
            ticket_status.value,
            ticket_id,
        ),
    )
    capture_ticket_attention(conn, ticket_id, now)


def _resting_status_for_ticket(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> TicketStatus:
    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    resting = (
        TicketStatus.empty
        if ownership_mode is None
        else machine.resting_ticket_status(ownership_mode)
    )
    return _blocked_standin(conn, ticket.id, resting)


def _entered_stage_status_for_ticket(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> TicketStatus:
    entered = TicketStatus.empty
    return _blocked_standin(conn, ticket.id, entered)


def _write_resting_ticket_status(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
    now: int,
) -> None:
    target_status = _resting_status_for_ticket(
        conn,
        ticket,
        worker_type_definition=worker_type_definition,
    )
    if ticket.ticket_status is target_status:
        return
    _write_ticket_status(
        conn,
        ticket.id,
        target_status,
        now,
    )


def _write_entered_stage_ticket_status(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
    now: int,
) -> None:
    target_status = _entered_stage_status_for_ticket(
        conn,
        ticket,
        worker_type_definition=worker_type_definition,
    )
    if ticket.ticket_status is target_status:
        return
    _write_ticket_status(conn, ticket.id, target_status, now)


def write_ticket_conversation_start(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    conversation_id: str,
    backend: str,
    model: str | None,
    reasoning_effort: str | None,
    now: int,
) -> Ticket:
    """Point the Ticket at the conversation just started for it, and record what it
    runs on. Returns the Ticket as it now stands, which names whichever conversation won.

    ``conversation_id`` is the Ticket's conversation link. Under the new
    conversation system the value it holds is the caller-owned conversation id, not an
    ACP session id: the conversation system rebinds its own backend sessions behind that
    one name. The three launch columns are the Ticket's last-chosen values — kept up to
    date with what the conversation actually runs on, so a fresh conversation starts
    from where the last one ended.

    The link only lands on a Ticket that has none. A Ticket has one conversation, and two
    callers can decide to make one at the same moment — the readiness loop with a step to
    send and a person typing into the panel. Overwriting would leave the loser's
    conversation live, linked to nothing, with an agent running in the same folder. The
    caller reads the returned Ticket to see whether it was the one that landed.
    """
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        updated = conn.execute(
            "UPDATE tickets SET conversation_id = ?, employee_backend = ?, "
            "employee_launch_model = ?, employee_launch_reasoning_effort = ?, "
            "updated_at = ? WHERE id = ? AND conversation_id IS NULL",
            (conversation_id, backend, model, reasoning_effort, now, ticket_id),
        )
        if updated.rowcount == 1:
            conn.execute(
                "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
                (conversation_id, ticket_id),
            )
        return _load_ticket_for_write(conn, ticket_id)


def remove_ticket_conversation_start(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    conversation_id: str,
    now: int,
) -> None:
    """Undo the exact provisional association for a first message that did not land."""
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        conn.execute(
            "UPDATE tickets SET conversation_id = NULL, updated_at = ? "
            "WHERE id = ? AND conversation_id = ?",
            (now, ticket_id, conversation_id),
        )
        conn.execute(
            "DELETE FROM ticket_conversations WHERE ticket_id = ? AND conversation_id = ?",
            (ticket_id, conversation_id),
        )


def write_ticket_last_chosen_configuration(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    expected_conversation_id: str,
    model: str | None,
    reasoning_effort: str | None,
    now: int,
) -> bool:
    """Record the model and reasoning effort a Ticket's conversation now runs on.

    ``expected_conversation_id`` names the conversation these values are true of, and the
    write only lands while the Ticket still points at it. Sending is awaited, and a Ticket
    can be pointed at a fresh conversation while a send into the old one is still out —
    without the guard, that send's model would be stamped onto a conversation that never
    ran on it. Reports whether the write fired. Not firing is not a failure: the values
    were never claimed to be true of whatever the Ticket moved on to.

    The backend is not here because a message cannot change it: a conversation keeps the
    backend it was started on, and choosing another one is a new conversation.
    """
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        updated = conn.execute(
            "UPDATE tickets SET employee_launch_model = ?, "
            "employee_launch_reasoning_effort = ?, updated_at = ? "
            "WHERE id = ? AND conversation_id = ?",
            (model, reasoning_effort, now, ticket_id, expected_conversation_id),
        )
        return updated.rowcount == 1


def clear_ticket_conversation_link(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    expected_conversation_id: str,
    now: int,
) -> bool:
    """Unlink the Ticket from the named conversation, and nothing else.

    ``expected_conversation_id`` is the conversation the caller acted on, and the unlink
    only lands while the Ticket still points at it. Killing a conversation is awaited, and
    a Ticket can be pointed at a fresh one in that time — an unguarded clear would then
    cut the Ticket loose from a conversation nobody killed. Reports whether the unlink
    fired. Not firing is not a failure: the Ticket had already moved on by itself.

    The last-chosen launch columns deliberately stay: they are exactly what the next
    conversation starts from.
    """
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        updated = conn.execute(
            "UPDATE tickets SET conversation_id = NULL, updated_at = ? "
            "WHERE id = ? AND conversation_id = ?",
            (now, ticket_id, expected_conversation_id),
        )
        return updated.rowcount == 1


def employee_launch_configuration(ticket: Ticket) -> EmployeeLaunchConfiguration:
    return EmployeeLaunchConfiguration(
        employee_backend=ticket.employee_backend,
        employee_launch_model=ticket.employee_launch_model,
        employee_launch_reasoning_effort=ticket.employee_launch_reasoning_effort,
    )


def employee_configuration_editable(ticket: Ticket) -> bool:
    """Whether this Ticket's launch values may still be changed.

    A Ticket that names a conversation is frozen: those values are what that conversation
    was started on, and there is no changing them after the fact. A Ticket with a worker
    step out is frozen too, because the values are about to be what a conversation was
    started on. Everything else is a question about the Ticket in hand, so this asks the
    database nothing.

    There is no test on the Stage. There used to be one, and it said `needs_kickoff`,
    which read as "only a Ticket nobody has started yet". The conversation was always the
    real reason, and a Ticket keeps one conversation across its worker steps, so naming
    no conversation already means pristine — or reset, which is the case this widening is
    for. A reset Ticket is exactly one whose next conversation has not been started, and
    choosing what that one runs on is the point of restarting it.
    """
    return ticket.conversation_id is None and ticket.ticket_status is not TicketStatus.agent


def write_employee_configuration(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    expected_employee_configuration: EmployeeLaunchConfiguration,
    employee_backend: str,
    employee_launch_model: str,
    employee_launch_reasoning_effort: str | None,
    advertised_models: frozenset[str] | None,
    reasoning_supported: bool | None,
    advertised_reasoning_efforts: frozenset[str] | None,
    now: int,
) -> Ticket:
    """Atomically replace the complete launch request during pristine Kickoff.

    There is no guard here against a conversation being started underneath this write,
    and none is needed. The Ticket's conversation link is a column on the row this
    transaction is already updating, so the two serialize. The old layer needed a
    compare-and-swap because the link lived in a table of its own — two tables, two
    transactions — and that is the reason it is gone rather than something to add back.
    """

    with _txn(conn):
        registered_backend = require_conversation_backend_key(employee_backend)
        ticket = _load_ticket_for_write(conn, ticket_id)
        current = employee_launch_configuration(ticket)
        if current != expected_employee_configuration:
            raise PlannerError(
                ErrorCode.already_running,
                "Employee configuration changed while its options were loading",
                {"ticket_id": ticket_id},
            )
        candidate = EmployeeLaunchConfiguration(
            employee_backend=registered_backend,
            employee_launch_model=employee_launch_model,
            employee_launch_reasoning_effort=employee_launch_reasoning_effort,
        )
        normalized = employee_configuration.normalize_employee_launch_configuration(
            current,
            candidate,
            advertised_models=advertised_models,
            reasoning_supported=reasoning_supported,
            advertised_reasoning_efforts=advertised_reasoning_efforts,
        )
        if current == normalized:
            return ticket
        if not employee_configuration_editable(ticket):
            raise PlannerError(
                ErrorCode.already_running,
                "Employee configuration is frozen while a conversation or a worker step holds it",
                {"ticket_id": ticket_id},
            )
        conn.execute(
            "UPDATE tickets SET employee_backend = ?, employee_launch_model = ?, "
            "employee_launch_reasoning_effort = ?, "
            "updated_at = ? WHERE id = ?",
            (
                normalized.employee_backend,
                normalized.employee_launch_model,
                normalized.employee_launch_reasoning_effort,
                now,
                ticket_id,
            ),
        )
        return _load_ticket_for_write(conn, ticket_id)


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    principal: Principal,
    now: int,
    title_max_chars: int,
    kickoff_note: str | None = "",
    project_id: str | None = None,
    sprint_id: str | None = None,
    priority: Priority | None = None,
    deadline: str | None = None,
    sprint_item_id: str | None = None,
    worker_type: str,
    employee_backend: str | None = None,
    employee_launch_model: str | None = None,
    worker_runtime_definitions: ConfiguredWorkerRuntimeDefinitions | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    day_id: str | None = None,
    stated_ceiling: str | None = None,
    stated_at_cap: AtCap | None = None,
) -> Ticket:
    admission.validate_title(title, title_max_chars)
    admission.validate_deadline(deadline)
    runtime_definitions = worker_runtime_definitions or configured_worker_runtime_definitions()
    worker_type_definition = runtime_definitions.worker_type_registry.require(worker_type)
    launch_defaults = read_worker_launch_defaults_for_ticket_creation(
        conn, runtime_definitions.worker_type_registry, worker_type
    )
    launch_configuration = employee_configuration.launch_configuration_for_a_new_ticket(
        default_backend=launch_defaults.employee_backend,
        default_model=launch_defaults.employee_launch_model,
        default_reasoning_effort=launch_defaults.employee_launch_reasoning_effort,
        employee_backend=require_conversation_backend_key(
            employee_backend if employee_backend is not None else launch_defaults.employee_backend
        ),
        employee_launch_model=employee_launch_model,
    )
    initial_stage = worker_type_definition.default_ceiling()
    default_ceiling = worker_type_definition.default_ceiling()
    ticket_id = new_id(ID_PREFIXES["ticket"])
    with _txn(conn):
        _validate_ceiling_holder(conn, principal, ticket_id=ticket_id)
        if sprint_item_id is not None and project_id is None:
            item = conn.execute(
                "SELECT project_id FROM sprint_items WHERE id = ?",
                (sprint_item_id,),
            ).fetchone()
            if item is not None:
                project_id = str(item["project_id"])
        _validate_ticket_creation_placement(
            conn,
            project_id=project_id,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
            blocked_by_ticket_ids=blocked_by_ticket_ids,
        )
        priority_anchors = _resolve_priority_anchors(
            conn, project_id=project_id, sprint_item_id=sprint_item_id
        )
        stored_priority = _created_ticket_priority(priority, priority_anchors)
        ceiling = (
            default_ceiling
            if stated_ceiling is None
            else worker_type_definition.resolve_ceiling(stated_ceiling)
        )
        at_cap = stated_at_cap or AtCap.propose
        stage, initial_values, initial_proposal, initial_ticket_status = _seed_kickoff(
            kickoff_note,
            principal,
            now,
            stage=initial_stage,
            ceiling=ceiling,
            worker_type_definition=worker_type_definition,
        )
        values_json = fields_codec.values_to_json(initial_values)
        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, employee_backend, employee_launch_model, "
            "employee_launch_reasoning_effort, stage, priority, deadline, "
            "project_id, sprint_id, sprint_item_id, "
            "recap, ceiling, ceiling_holder, at_cap, "
            "ticket_status, conversation_id, field_values, pending_proposal, "
            "created_at, updated_at, ticket_status_changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, NULL, "
            "?, ?, ?, ?, ?)",
            (
                ticket_id,
                title,
                worker_type,
                launch_configuration.employee_backend,
                launch_configuration.employee_launch_model,
                launch_configuration.employee_launch_reasoning_effort,
                stage,
                stored_priority.value,
                deadline,
                project_id,
                sprint_id,
                sprint_item_id,
                ceiling,
                _principal_to_json(principal),
                at_cap.value,
                initial_ticket_status.value,
                values_json,
                fields_codec.proposal_to_json(initial_proposal),
                now,
                now,
                now,
            ),
        )
        if day_id is not None:
            days_data.add_day_ticket(conn, day_id, ticket_id, now)
        for blocker_ticket_id in blocked_by_ticket_ids or []:
            ticket_blocks.add_ticket_block(conn, blocker_ticket_id, ticket_id, now)
        return _load_ticket_for_write(conn, ticket_id)


def audit_ticket_registry_integrity(conn: sqlite3.Connection) -> None:
    """Validate saved values and the one pending proposal before background work starts."""
    for row in conn.execute("SELECT id FROM tickets ORDER BY id"):
        try:
            ticket, _definition = _load_ticket_and_worker_type_definition_for_write(
                conn, str(row["id"])
            )
            require_conversation_backend_key(ticket.employee_backend)
            if (
                ticket.ticket_status is TicketStatus.awaiting_approval
                and ticket.pending_proposal is None
            ):
                raise PlannerError(ErrorCode.validation, "awaiting approval without a proposal")
        except (PlannerError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"ticket integrity audit failed: id={row['id']} reason={exc}"
            ) from exc


def read_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    return _load_ticket(conn, ticket_id)


def read_ticket_by_conversation_id(conn: sqlite3.Connection, conversation_id: str) -> Ticket:
    """Resolve the Ticket that owns this durable Employee conversation."""
    rows = conn.execute(
        "SELECT tickets.*, projects.name AS project_name, "
        "projects.priority AS priority_project_priority, "
        "sprint_items.title AS priority_sprint_item_title, "
        "sprint_items.priority AS priority_sprint_item_priority "
        "FROM tickets LEFT JOIN sprint_items ON sprint_items.id = tickets.sprint_item_id "
        "LEFT JOIN projects ON projects.id = tickets.project_id "
        "JOIN ticket_conversations ON ticket_conversations.ticket_id = tickets.id "
        "WHERE ticket_conversations.conversation_id = ? ORDER BY tickets.id",
        (conversation_id,),
    ).fetchall()
    if not rows:
        raise PlannerError(
            ErrorCode.not_found,
            "no ticket owns this Employee session",
            {"conversation_id": conversation_id},
        )
    if len(rows) > 1:
        raise PlannerError(
            ErrorCode.validation,
            "multiple tickets own this Employee session",
            {
                "conversation_id": conversation_id,
                "ticket_ids": sorted(str(row["id"]) for row in rows),
            },
        )
    return _row_to_ticket(rows[0])


def get_effective_sprint_id(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    return _load_ticket(conn, ticket_id).effective_sprint_id


def claim_ticket_for_worker_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    planning_day_id_resolver: Callable[[], str],
    readiness_check: _WorkerStepReadinessCheck,
    now: int,
) -> Ticket | None:
    """Take this Ticket out of ``empty`` for one worker step, or report it is not ready.

    The status flip IS the claim: there is no claim stamp and no separate run row. The
    readiness check runs again here, inside the write transaction, where its answer is
    final — two racing callers both re-check under the same write lock and only the one
    that finds the Ticket still at ``empty`` writes.

    Returns the claimed Ticket, whose ``ticket_status`` and ``ticket_status_revision``
    are what ``release_worker_step_claim`` must be given to give the claim back.
    """
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        planning_day_id = planning_day_id_resolver()
        if not readiness_check(
            conn,
            ticket,
            planning_day_id=planning_day_id,
            worker_type_definition=worker_type_definition,
        ):
            return None
        ownership_mode = machine.stage_ownership_mode(
            ticket.stage,
            worker_type_definition=worker_type_definition,
        )
        if ownership_mode is None:
            raise PlannerError(
                ErrorCode.validation,
                "a terminal stage has no worker step to claim",
                {"ticket_id": ticket_id, "stage": ticket.stage},
            )
        if ownership_mode is StageOwnershipMode.user:
            conn.execute(
                "INSERT INTO ticket_paired_stage_openers(ticket_id, stage, opened_at) "
                "VALUES (?, ?, ?)",
                (ticket_id, ticket.stage, now),
            )
        _write_ticket_status(
            conn,
            ticket_id,
            machine.worker_step_departure_status(ownership_mode),
            now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def forget_user_stage_opener(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    stage: str,
) -> None:
    """Re-arm a user-owned Stage when its tentative opener reached nobody."""
    with _txn(conn):
        conn.execute(
            "DELETE FROM ticket_paired_stage_openers WHERE ticket_id = ? AND stage = ?",
            (ticket_id, stage),
        )


def clear_ticket_error_for_restart(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
) -> Ticket:
    """Clear an error only through the explicit restart path."""
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.ticket_status is not TicketStatus.errored:
            return ticket
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return _load_ticket_for_write(conn, ticket_id)


def release_worker_step_claim(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    expected_status: TicketStatus,
    expected_status_revision: int,
    now: int,
) -> bool:
    """Give a worker-step claim back, but only if the Ticket has not moved on since.

    Both the departure status and its monotonic revision must still match. A later
    transition can return to the same status within the same second; its revision
    still differs, so an old release cannot erase the fresh claim.

    Reports whether the release actually fired. The flip goes back through the one status
    write door, so a Ticket that has since acquired a live blocker lands on ``blocked``
    rather than ``empty``, exactly as any other return to rest does.
    """
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.ticket_status is not expected_status:
            return False
        if ticket.ticket_status_revision != expected_status_revision:
            return False
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return True


def mark_ticket_errored(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
) -> Ticket:
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        _write_ticket_status(conn, ticket_id, TicketStatus.errored, now)
        return _load_ticket_for_write(conn, ticket_id)


def file_current_proposal_with_recap(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    recap: str,
    principal: Principal,
    now: int,
) -> Ticket:
    """Worker proposal surface: infer the current gating field and update recap atomically.

    Standalone recap keeps its normal "past needs_success" guard. A proposal always carries
    a recap, so this writer validates and writes it in the same transaction even when the
    proposal parks at the first success gate.
    """
    if principal != Principal(PrincipalKind.ticket, ticket_id):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "only the Ticket's own Worker can file its proposal",
            {"ticket_id": ticket_id},
        )
    admission.validate_body(recap, "recap")
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_file_proposal(
            ticket,
            body,
            principal,
            now,
            worker_type_definition=worker_type_definition,
        )
        _apply_decision(conn, ticket, decision, now)
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
            (recap, now, ticket_id),
        )
        if decision.pending_proposal is not None:
            _write_ticket_status(conn, ticket_id, TicketStatus.awaiting_approval, now)
        else:
            updated = _load_ticket_for_write(conn, ticket_id)
            if decision.stage != ticket.stage:
                _write_entered_stage_ticket_status(
                    conn,
                    updated,
                    worker_type_definition=worker_type_definition,
                    now=now,
                )
            else:
                _write_resting_ticket_status(
                    conn,
                    updated,
                    worker_type_definition=worker_type_definition,
                    now=now,
                )
        return _load_ticket_for_write(conn, ticket_id)


def accept_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    principal: Principal,
    now: int,
    edited_body: str | None = None,
    next_ceiling: NextCeiling | None = None,
    at_cap: AtCap | None = None,
    next_holder: Principal,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id, principal)
        decision = resolution.decide_accept(
            ticket,
            field,
            principal,
            edited_body,
            next_ceiling,
            at_cap,
            next_holder,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        if decision.stage != ticket.stage:
            _write_entered_stage_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
        else:
            _write_resting_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
        return _load_ticket_for_write(conn, ticket_id)


def edit_pending_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    new_body: str,
    principal: Principal,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket, definition = _load_ticket_and_worker_type_definition_for_write(conn, ticket_id)
        decision = resolution.decide_edit_pending_proposal(
            ticket, field, new_body, principal, worker_type_definition=definition
        )
        updated = _apply_decision(conn, ticket, decision, now)
        return updated


def edit_field_value(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    new_body: str,
    principal: Principal,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_edit_value(
            ticket,
            field,
            new_body,
            principal,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        if decision.stage != ticket.stage:
            _write_entered_stage_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
            return _load_ticket_for_write(conn, ticket_id)
        return updated


def require_return_for_revision(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    principal: Principal,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    """Run every current authorization check without sending or writing."""
    ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
        conn, ticket_id
    )
    _require_current_supervisor_parent(
        conn,
        ticket,
        supervisor_sprint_item_id,
        principal,
        "Ticket proposal rejection",
    )
    resolution.decide_return_for_revision(
        ticket,
        principal,
        worker_type_definition=worker_type_definition,
    )
    return ticket


def return_for_revision(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    principal: Principal,
    now: int,
    expected_proposal: PendingTicketProposal | None = None,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    admission.validate_revision_guidance(message)
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id, principal)
        field = worker_type_definition.gating_field(ticket.stage)
        current_proposal = ticket.pending_proposal
        if expected_proposal is not None and current_proposal != expected_proposal:
            raise PlannerError(
                ErrorCode.validation,
                "proposal changed before revision guidance was applied",
                {"ticket_id": ticket_id, "field": field},
            )
        decision = resolution.decide_return_for_revision(
            ticket,
            principal,
            worker_type_definition=worker_type_definition,
        )
        revision_feedback.set_feedback(
            conn,
            ticket_id,
            stage=ticket.stage,
            sender=principal,
            message=message,
            now=now,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        conn.execute(
            "DELETE FROM ticket_paired_stage_openers WHERE ticket_id = ? AND stage = ?",
            (ticket_id, ticket.stage),
        )
        _write_resting_ticket_status(
            conn,
            updated,
            worker_type_definition=worker_type_definition,
            now=now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def drop_ticket(
    conn: sqlite3.Connection, ticket_id: str, *, principal: Principal, now: int
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_drop(ticket, principal)
        updated = _apply_decision(conn, ticket, decision, now)
        _write_resting_ticket_status(
            conn,
            updated,
            worker_type_definition=worker_type_definition,
            now=now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def delete_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    principal: Principal,
    now: int,
    even_while_running: bool = False,
    supervisor_sprint_item_id: str | None = None,
) -> TicketDeletion:
    """Permanently remove a mistaken ticket and its product footprint in one transaction.

    Deletion is blocked while the Ticket's status says a worker step is out. Whether the
    Ticket's conversation is live is a question for the conversation system, so the route
    asks it before calling this writer; this writer stays a pure database transaction.

    `even_while_running` deletes a Ticket the status still calls claimed. The user asks
    for it with `--force`, for a status stranded at `agent` with no worker behind it, and
    a Sprint Item supervisor deleting its own child Ticket always has it. It skips that
    one guard: the actor check above still runs, and silencing a live worker is the
    route's, since only the conversation system knows one is there.

    A Sprint Item supervisor deletes only a current child of its own Item. The route
    admits it and names that Item here, and the parent is rechecked inside the
    transaction, because a Ticket can move between the two.
    """
    admission.require_direct_or_supervisor_principal(principal, "delete_ticket")
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        _require_current_supervisor_parent(
            conn, ticket, supervisor_sprint_item_id, principal, "Ticket deletion"
        )
        held_elsewhere = conn.execute(
            "SELECT id FROM tickets WHERE id != ? "
            "AND json_extract(ceiling_holder, '$.kind') = 'ticket' "
            "AND json_extract(ceiling_holder, '$.id') = ? ORDER BY id LIMIT 1",
            (ticket_id, ticket_id),
        ).fetchone()
        if held_elsewhere is not None:
            raise PlannerError(
                ErrorCode.validation,
                "ticket cannot be deleted while it holds another Ticket ceiling",
                {"ticket_id": ticket_id, "held_ticket_id": str(held_elsewhere["id"])},
            )
        if not even_while_running and ticket.ticket_status is TicketStatus.agent:
            raise PlannerError(
                ErrorCode.already_running,
                "ticket activity is still running",
                {"ticket_id": ticket_id},
            )

        day_ids = tuple(
            str(row["day_id"])
            for row in conn.execute(
                "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id",
                (ticket_id,),
            ).fetchall()
        )
        ticket_block_rows = conn.execute(
            "SELECT blocking_ticket_id, blocked_ticket_id FROM ticket_blocks "
            "WHERE blocking_ticket_id = ? OR blocked_ticket_id = ? "
            "ORDER BY blocking_ticket_id, blocked_ticket_id",
            (ticket_id, ticket_id),
        ).fetchall()
        linked_ticket_ids = tuple(
            sorted(
                {
                    str(
                        row["blocked_ticket_id"]
                        if row["blocking_ticket_id"] == ticket_id
                        else row["blocking_ticket_id"]
                    )
                    for row in ticket_block_rows
                }
            )
        )
        sprint_item_ids = (ticket.sprint_item_id,) if ticket.sprint_item_id is not None else ()
        effective_sprint_id = ticket.effective_sprint_id
        sprint_ids = (effective_sprint_id,) if effective_sprint_id is not None else ()

        for day_id in day_ids:
            days_data.remove_day_ticket(conn, day_id, ticket_id, now)
        for row in ticket_block_rows:
            conn.execute(
                "DELETE FROM ticket_blocks "
                "WHERE blocking_ticket_id = ? AND blocked_ticket_id = ?",
                (str(row["blocking_ticket_id"]), str(row["blocked_ticket_id"])),
            )
        for row in ticket_block_rows:
            if str(row["blocking_ticket_id"]) == ticket_id:
                settle_blocked_standin_for_ticket(conn, str(row["blocked_ticket_id"]), now)

        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        return TicketDeletion(
            ticket_id=ticket_id,
            title=ticket.title,
            day_ids=day_ids,
            sprint_item_ids=sprint_item_ids,
            sprint_ids=sprint_ids,
            linked_ticket_ids=linked_ticket_ids,
        )


def change_scope(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    ceiling: str,
    at_cap: AtCap,
    principal: Principal,
    now: int,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    if principal.kind is PrincipalKind.sprint_item and supervisor_sprint_item_id is None:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "change_scope requires the Sprint Item supervisor parent",
            {"actor": principal_legacy_actor(principal), "ticket_id": ticket_id},
        )
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id, principal)
        decision = resolution.decide_scope_change(
            ticket,
            ceiling,
            at_cap,
            principal,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        if ticket.ticket_status in {
            TicketStatus.empty,
            TicketStatus.blocked,
        }:
            _write_resting_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
        return _load_ticket_for_write(conn, ticket_id)


def replace_guidance(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    principal: Principal,
    now: int,
) -> Ticket:
    """Replace the Ticket's durable guidance without changing its workflow."""
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        conn.execute(
            "UPDATE tickets SET guidance = ?, updated_at = ? WHERE id = ?",
            (body, now, ticket_id),
        )
        return _load_ticket_for_write(conn, ticket_id)


def append_guidance(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    principal: Principal,
    now: int,
) -> Ticket:
    """Append in one transaction; an empty append changes nothing."""
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if not body:
            return ticket
        guidance = f"{ticket.guidance}\n\n{body}" if ticket.guidance else body
        conn.execute(
            "UPDATE tickets SET guidance = ?, updated_at = ? WHERE id = ?",
            (guidance, now, ticket_id),
        )
        return _load_ticket_for_write(conn, ticket_id)


def edit_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    edit: TicketEdit,
    title_max_chars: int,
    principal: Principal,
    now: int,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    if principal.kind is PrincipalKind.sprint_item and supervisor_sprint_item_id is None:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "edit_ticket requires the Sprint Item supervisor parent",
            {"actor": principal_legacy_actor(principal), "ticket_id": ticket_id},
        )
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id, principal)

        title = edit["title"] if "title" in edit else ticket.title
        priority = edit["priority"] if "priority" in edit else ticket.priority
        deadline = edit["deadline"] if "deadline" in edit else ticket.deadline
        project_id = edit["project_id"] if "project_id" in edit else ticket.project_id
        sprint_id = edit["sprint_id"] if "sprint_id" in edit else ticket.sprint_id
        sprint_item_id = (
            edit["sprint_item_id"] if "sprint_item_id" in edit else ticket.sprint_item_id
        )

        # Validate the intended final Ticket before its first durable effect. Parent
        # restrictions use request-key presence: explicitly assigning the same/null
        # derived value is still an attempted edit and retains the existing error.
        admission.validate_title(title, title_max_chars)
        admission.validate_deadline(deadline)
        _validate_ticket_creation_placement(
            conn,
            project_id=project_id,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
            blocked_by_ticket_ids=None,
        )
        candidates: tuple[tuple[str, str, str | int | None, str | int | None], ...] = (
            ("title", "title", ticket.title, title),
            ("priority", "priority", ticket.priority.value, priority.value),
            ("deadline", "deadline", ticket.deadline, deadline),
            ("project_id", "project_id", ticket.project_id, project_id),
            ("sprint_id", "sprint_id", ticket.sprint_id, sprint_id),
            ("sprint_item_id", "sprint_item_id", ticket.sprint_item_id, sprint_item_id),
        )
        changes = [change for change in candidates if change[2] != change[3]]
        if not changes:
            return ticket

        assignments = ", ".join(f"{column} = ?" for _field, column, _old, _new in changes)
        params = [new for _field, _column, _old, new in changes]
        conn.execute(
            f"UPDATE tickets SET {assignments}, updated_at = ? WHERE id = ?",
            (*params, now, ticket_id),
        )
        return _load_ticket_for_write(conn, ticket_id)


def write_recap(
    conn: sqlite3.Connection, ticket_id: str, *, body: str, principal: Principal, now: int
) -> Ticket:
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
            (body, now, ticket_id),
        )
        return _load_ticket_for_write(conn, ticket_id)


def classify_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    sprint_item_id: str,
    principal: Principal,
    now: int,
    admit: Callable[[], None] | None = None,
) -> Ticket:
    with _txn(conn):
        if admit is not None:
            admit()
        ticket = _load_ticket_for_write(conn, ticket_id)
        item = conn.execute(
            "SELECT project_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is None:
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        if ticket.sprint_item_id == sprint_item_id:
            return ticket
        conn.execute(
            "UPDATE tickets SET sprint_item_id = ?, project_id = ?, updated_at = ? WHERE id = ?",
            (sprint_item_id, str(item["project_id"]), now, ticket_id),
        )
        return _load_ticket_for_write(conn, ticket_id)


def unclassify_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    sprint_item_id: str,
    principal: Principal,
    now: int,
    admit: Callable[[], None] | None = None,
) -> Ticket:
    with _txn(conn):
        if admit is not None:
            admit()
        ticket = _load_ticket_for_write(conn, ticket_id)
        item = conn.execute(
            "SELECT project_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is None:
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        # Removal is a compare-and-clear operation. A retry after the first removal is a
        # no-op, and a stale retry after another request moved the ticket elsewhere must
        # not detach that newer membership.
        if ticket.sprint_item_id is None:
            return ticket
        if ticket.sprint_item_id != sprint_item_id:
            raise PlannerError(ErrorCode.validation, "Ticket has a different Outcome")
        conn.execute(
            "UPDATE tickets SET sprint_item_id = NULL, updated_at = ? WHERE id = ?",
            (now, ticket_id),
        )
        return _load_ticket_for_write(conn, ticket_id)
