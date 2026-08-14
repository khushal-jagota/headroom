"""The only module that writes Ticket rows. Stage, ceiling/at_cap, and fields
value mutations happen in exactly one function (_apply_decision); every public
writer is one BEGIN IMMEDIATE transaction. An ordinary Ticket edit validates and
writes its requested plain attributes together. Other semantic writers remain
separate. sqlite3 and ids live here only; the clock arrives as now (unix
seconds) and the title limit as an argument."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

from planner.conversation.contracts import require_conversation_backend_key
from planner.core import links as core_links
from planner.core.contracts import EventKind, LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.days import data as days_data
from planner.tickets import worker_context as ticket_worker_context
from planner.tickets.contracts import (
    AtCap,
    EmployeeLaunchConfiguration,
    FieldSlot,
    NextCeiling,
    ProjectPriorityAnchor,
    Proposal,
    ProposalReviewRoute,
    ResolvedTicketPriorityAnchors,
    SprintItemPriorityAnchor,
    StageOwnershipMode,
    Ticket,
    TicketDeletion,
    TicketEdit,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import (
    admission,
    employee_configuration,
    external_work,
    fields_codec,
    machine,
    resolution,
)
from planner.tickets.logic.decisions import Decision
from planner.worker_settings.service import (
    database_parent_from_connection,
    read_stage_default_ownership_for_ticket_entry,
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
            "SELECT project_id, sprint_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is None:
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        if project_id != item["project_id"] or sprint_id != item["sprint_id"]:
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
                ErrorCode.link_invalid,
                "from_id must be an existing ticket",
                {"from_id": blocker_ticket_id},
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
            "SELECT project_id, sprint_id FROM sprint_items WHERE id = ?",
            (sprint_item_id,),
        ).fetchone()
        if item is not None:
            project_id = str(item["project_id"])
            sprint_id = item["sprint_id"]
    _validate_ticket_creation_placement(
        conn,
        project_id=project_id,
        sprint_id=sprint_id,
        sprint_item_id=sprint_item_id,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
    )


def _stage_ownership_overrides_from_json(raw: object) -> dict[str, StageOwnershipMode]:
    if raw is None:
        return {}
    try:
        payload = json.loads(str(raw))
    except ValueError as exc:
        raise RuntimeError("ticket stage_ownership_overrides is corrupt") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("ticket stage_ownership_overrides is not an object")
    overrides: dict[str, StageOwnershipMode] = {}
    for stage, mode in payload.items():
        if not isinstance(stage, str) or not isinstance(mode, str):
            raise RuntimeError("ticket stage_ownership_overrides has invalid entries")
        overrides[stage] = StageOwnershipMode(mode)
    return overrides


def _stage_ownership_overrides_to_json(
    overrides: Mapping[str, StageOwnershipMode],
) -> str:
    return json.dumps({stage: mode.value for stage, mode in sorted(overrides.items())})


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    worker_type = str(row["worker_type"])
    worker_type_definition = configured_worker_type_registry().require(worker_type)
    stage = str(row["stage"])
    overrides = _stage_ownership_overrides_from_json(row["stage_ownership_overrides"])
    default_ownership = (
        None
        if row["default_stage_ownership_mode"] is None
        else StageOwnershipMode(str(row["default_stage_ownership_mode"]))
    )
    effective_ownership = machine.effective_stage_ownership_mode(
        stage,
        overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=default_ownership,
        ceiling=str(row["ceiling"]),
        at_cap=AtCap(str(row["at_cap"])),
    )
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
        ceiling=str(row["ceiling"]),
        at_cap=AtCap(row["at_cap"]),
        ticket_status=TicketStatus(row["ticket_status"]),
        ticket_status_changed_at=int(row["ticket_status_changed_at"]),
        ticket_status_revision=int(row["ticket_status_revision"]),
        backend_error=(str(row["backend_error"]) if row["backend_error"] is not None else None),
        stage_ownership_overrides=overrides,
        default_stage_ownership_mode=default_ownership,
        effective_stage_ownership_mode=effective_ownership,
        conversation_id=row["conversation_id"],
        alias=row["alias"],
        fields=fields_codec.fields_from_json(row["fields"]),
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
    fields_codec.declared_fields_from_json(str(row["fields"]), worker_type_definition.field_ids())
    return _row_to_ticket(row), worker_type_definition


def _load_ticket_for_write(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    ticket, _worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
        conn, ticket_id
    )
    return ticket


def _is_normal_sprint_item(conn: sqlite3.Connection, sprint_item_id: str | None) -> bool:
    if sprint_item_id is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM sprint_items WHERE id = ? AND kind = 'normal'",
        (sprint_item_id,),
    ).fetchone()
    return row is not None


def _seed_kickoff(
    kickoff_note: str | None,
    actor: str,
    now: int,
    *,
    stage: str,
    ceiling: str,
    at_cap: AtCap,
    ownership_mode: StageOwnershipMode | None,
    worker_type_definition: WorkerTypeDefinition,
) -> tuple[str, TicketFields, TicketStatus]:
    """Seed the kickoff under the scope the creator stated.

    Scope stated at creation is the same scope an ordinary proposal is judged against, so
    a kickoff settles, parks for its agent reviewer, or parks for the user by exactly the
    rules a later proposal follows. Nothing parks that the creator cannot resolve.
    """
    fields = TicketFields.empty(worker_type_definition.field_ids())
    if kickoff_note is None:
        return stage, fields, TicketStatus.empty
    effective_ownership = ownership_mode or StageOwnershipMode.worker
    settled_stage = (
        machine.auto_accept_target(
            stage, ceiling, "kickoff", worker_type_definition=worker_type_definition
        )
        if effective_ownership is StageOwnershipMode.worker
        else None
    )
    if settled_stage is not None:
        fields = fields_codec.with_slot(
            fields, "kickoff", FieldSlot(value=kickoff_note, proposal=None, user_note=None)
        )
        return settled_stage, fields, machine.resting_ticket_status(effective_ownership)
    review_route = machine.parked_proposal_review_route(effective_ownership, at_cap)
    fields = fields_codec.with_slot(
        fields,
        "kickoff",
        FieldSlot(
            value=None,
            proposal=Proposal(
                body=kickoff_note,
                proposed_by=actor,
                created_at=now,
            ),
            user_note=None,
        ),
    )
    status = (
        TicketStatus.awaiting_agent_review
        if review_route is ProposalReviewRoute.agent_review
        else TicketStatus.awaiting_user_review
    )
    return stage, fields, status


def _require_agent_review_placement(
    conn: sqlite3.Connection,
    ticket: Ticket,
    at_cap: AtCap | None,
    sprint_item_id: str | None,
) -> None:
    """Keep agent review paired with the normal Sprint Item that owns its reviewer.

    A parked proposal needs no separate check. Its reviewer is derived from at_cap, so a
    Ticket outside agent review holds nothing an agent reviews.
    """
    if at_cap is not AtCap.agent_review:
        return
    row = conn.execute(
        "SELECT 1 FROM sprint_items WHERE id = ? AND kind = 'normal'",
        (sprint_item_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.scope_invalid,
            "agent review requires placement under a normal Sprint Item",
            {"ticket_id": ticket.id, "sprint_item_id": sprint_item_id},
        )


def _require_current_supervisor_parent(
    conn: sqlite3.Connection,
    ticket: Ticket,
    sprint_item_id: str | None,
) -> None:
    """Recheck exact current parent while the resolving write holds its transaction."""
    if sprint_item_id is None:
        return
    row = conn.execute(
        "SELECT 1 FROM sprint_items AS item "
        "WHERE item.id = ? AND item.kind = 'normal' AND ? = item.id",
        (sprint_item_id, ticket.sprint_item_id),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "Ticket review is not available to this Sprint Item supervisor",
            {
                "actor": admission.SPRINT_ITEM_SUPERVISOR_ACTOR,
                "sprint_item_id": sprint_item_id,
                "ticket_id": ticket.id,
            },
        )


def _active_blocker_stage(stage: str) -> bool:
    return stage not in {"done", "dropped"}


def _default_stage_ownership_for_entry(
    conn: sqlite3.Connection,
    worker_type_definition: WorkerTypeDefinition,
    stage: str,
) -> StageOwnershipMode | None:
    if worker_type_definition.is_terminal(stage):
        return None
    database_parent = database_parent_from_connection(conn)
    if database_parent is None:
        return worker_type_definition.stage_definition(stage).default_ownership_mode
    return read_stage_default_ownership_for_ticket_entry(
        database_parent,
        configured_worker_type_registry(),
        worker_type_definition.worker_type,
        stage,
    )


def _outgoing_block_target_ids(conn: sqlite3.Connection, ticket_id: str) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT to_id FROM links WHERE from_id = ? AND kind = 'blocks' ORDER BY to_id",
        (ticket_id,),
    ).fetchall()
    return tuple(str(row["to_id"]) for row in rows)


def _has_live_blocker(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """Whether a blocks link into this Ticket has a source that is not done or dropped."""
    return (
        conn.execute(
            "SELECT 1 FROM links "
            "JOIN tickets source ON source.id = links.from_id "
            "WHERE links.kind = 'blocks' AND links.to_id = ? "
            "AND source.stage NOT IN ('done', 'dropped') LIMIT 1",
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

    The single transition writer for the link-driven pair. A no-op unless the Ticket is
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


def settle_blocked_standin_for_link_target(
    conn: sqlite3.Connection, target_id: str, now: int
) -> None:
    """Settle a blocks-link target. Targets may be Tickets or sprint items; only a
    Ticket carries a ticket status, so a sprint-item target is skipped."""
    if target_id.split("_", 1)[0] != ID_PREFIXES["ticket"]:
        return
    settle_blocked_standin(conn, target_id, now)


def _release_outgoing_blocks_links(
    conn: sqlite3.Connection, ticket_id: str, target_ids: tuple[str, ...], now: int
) -> None:
    """A completing Ticket drops the blocks links it holds and frees each named target."""
    for target_id in target_ids:
        conn.execute(
            "DELETE FROM links WHERE from_id = ? AND to_id = ? AND kind = ?",
            (ticket_id, target_id, LinkKind.blocks.value),
        )
    for target_id in target_ids:
        settle_blocked_standin_for_link_target(conn, target_id, now)


def _apply_decision(
    conn: sqlite3.Connection, ticket: Ticket, decision: Decision, now: int
) -> Ticket:
    new_fields = decision.new_fields if decision.new_fields is not None else ticket.fields
    new_stage = decision.new_stage if decision.new_stage is not None else ticket.stage
    new_ceiling = decision.new_ceiling if decision.new_ceiling is not None else ticket.ceiling
    new_at_cap = decision.new_at_cap if decision.new_at_cap is not None else ticket.at_cap
    # Pre-persist door: the prospective (stage, ceiling) must be registry-valid for
    # this ticket's type before any SQL — the enforcement the dropped DB CHECKs gave.
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)
    worker_type_definition.validate_ticket_position(str(new_stage), str(new_ceiling))
    new_default_stage_ownership_mode = (
        _default_stage_ownership_for_entry(conn, worker_type_definition, str(new_stage))
        if str(new_stage) != ticket.stage
        else ticket.default_stage_ownership_mode
    )
    active_before = _active_blocker_stage(ticket.stage)
    active_after = _active_blocker_stage(new_stage)
    affected_blocked_target_ids: tuple[str, ...] = ()
    if active_before != active_after:
        affected_blocked_target_ids = _outgoing_block_target_ids(conn, ticket.id)
        if active_after:
            for target_id in affected_blocked_target_ids:
                if core_links.would_create_active_blocks_cycle(conn, ticket.id, target_id):
                    raise PlannerError(
                        ErrorCode.link_cycle,
                        "ticket stage change would activate a blocks cycle",
                        {"ticket_id": ticket.id, "to_id": target_id},
                    )
    conn.execute(
        "UPDATE tickets SET fields = ?, stage = ?, default_stage_ownership_mode = ?, "
        "ceiling = ?, at_cap = ?, updated_at = ? WHERE id = ?",
        (
            fields_codec.fields_to_json(new_fields),
            str(new_stage),
            (
                new_default_stage_ownership_mode.value
                if new_default_stage_ownership_mode is not None
                else None
            ),
            str(new_ceiling),
            new_at_cap.value,
            now,
            ticket.id,
        ),
    )
    if active_before != active_after:
        if active_after:
            # Reopened out of done: the links this Ticket still holds block again, so
            # each target re-derives its stand-in against the now-live source.
            for target_id in affected_blocked_target_ids:
                settle_blocked_standin_for_link_target(conn, target_id, now)
        else:
            # Completed into done/dropped: drop the blocks links this Ticket holds and
            # rewrite each named target's status in this same transaction.
            _release_outgoing_blocks_links(conn, ticket.id, affected_blocked_target_ids, now)
    return _load_ticket(conn, ticket.id)


def _write_ticket_status(
    conn: sqlite3.Connection,
    ticket_id: str,
    ticket_status: TicketStatus,
    now: int,
    *,
    error: str | None = None,
) -> None:
    # The one write door also carries the stand-in: a caller asking for `empty` on a
    # Ticket with a live blocker durably lands on `blocked`.
    ticket_status = _blocked_standin(conn, ticket_id, ticket_status)
    backend_error = error if ticket_status is TicketStatus.errored else None
    if ticket_status is TicketStatus.errored and not backend_error:
        raise ValueError("errored Ticket status requires a concrete backend error")
    # ticket_status_changed_at answers "how long has this Ticket been where it is",
    # so it moves only when the value really moves — rewriting the same status is not a
    # change. The CASE keeps that comparison against the stored row, in the one write.
    conn.execute(
        "UPDATE tickets SET ticket_status = ?, backend_error = ?, updated_at = ?, "
        "ticket_status_changed_at = CASE WHEN ticket_status = ? "
        "THEN ticket_status_changed_at ELSE ? END, "
        "ticket_status_revision = CASE WHEN ticket_status = ? "
        "THEN ticket_status_revision ELSE ticket_status_revision + 1 END WHERE id = ?",
        (
            ticket_status.value,
            backend_error,
            now,
            ticket_status.value,
            now,
            ticket_status.value,
            ticket_id,
        ),
    )
    # This projection shares the canonical status transaction. Reconciliation repairs
    # interrupted deployments, but it is not the source capture door.
    from planner.supervisor_obligations.data import project_ticket

    project_ticket(conn, ticket_id, now)


def _resting_status_for_ticket(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> TicketStatus:
    ownership_mode = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
        ceiling=ticket.ceiling,
        at_cap=ticket.at_cap,
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
    ownership_mode = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
        ceiling=ticket.ceiling,
        at_cap=ticket.at_cap,
    )
    entered = TicketStatus.user if ownership_mode is StageOwnershipMode.user else TicketStatus.empty
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


def _write_parked_proposal_status(
    conn: sqlite3.Connection,
    ticket_id: str,
    ticket: Ticket,
    field: str,
    now: int,
) -> None:
    proposal = fields_codec.get_slot(ticket.fields, field).proposal
    if proposal is None:
        raise PlannerError(ErrorCode.validation, "ticket has no parked proposal")
    route = machine.parked_proposal_review_route(
        ticket.effective_stage_ownership_mode or StageOwnershipMode.user,
        ticket.at_cap,
    )
    status = (
        TicketStatus.awaiting_agent_review
        if route is ProposalReviewRoute.agent_review
        else TicketStatus.awaiting_user_review
    )
    _write_ticket_status(conn, ticket_id, status, now)


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
    was started on, and there is no changing them after the fact. Everything else is a
    question about the Ticket in hand, so this asks the database nothing.
    """
    return (
        ticket.stage == "needs_kickoff"
        and ticket.ticket_status
        in {
            TicketStatus.awaiting_user_review,
            TicketStatus.paired,
            TicketStatus.empty,
            TicketStatus.blocked,
        }
        and ticket.conversation_id is None
    )


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
                "Employee configuration is frozen after Kickoff or the first worker session",
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
    actor: str,
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
    supervisor_sprint_item_id: str | None = None,
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
    default_stage_ownership_mode = _default_stage_ownership_for_entry(
        conn, worker_type_definition, initial_stage
    )
    ticket_id = new_id(ID_PREFIXES["ticket"])
    with _txn(conn):
        if sprint_item_id is not None and project_id is None and sprint_id is None:
            item = conn.execute(
                "SELECT project_id, sprint_id FROM sprint_items WHERE id = ?",
                (sprint_item_id,),
            ).fetchone()
            if item is not None:
                project_id = str(item["project_id"])
                sprint_id = item["sprint_id"]
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
        # A supervisor creating under its own Item reviews what it created.
        supervised = (
            actor == admission.SPRINT_ITEM_SUPERVISOR_ACTOR
            and sprint_item_id is not None
            and supervisor_sprint_item_id == sprint_item_id
            and _is_normal_sprint_item(conn, sprint_item_id)
        )
        ceiling = (
            default_ceiling
            if stated_ceiling is None
            else worker_type_definition.resolve_ceiling(stated_ceiling)
        )
        at_cap = stated_at_cap or (AtCap.agent_review if supervised else AtCap.user_review)
        if at_cap is AtCap.agent_review and not _is_normal_sprint_item(conn, sprint_item_id):
            raise PlannerError(
                ErrorCode.scope_invalid,
                "agent review requires placement under a normal Sprint Item",
                {"sprint_item_id": sprint_item_id},
            )
        stage, initial_fields, initial_ticket_status = _seed_kickoff(
            kickoff_note,
            actor,
            now,
            stage=initial_stage,
            ceiling=ceiling,
            at_cap=at_cap,
            ownership_mode=default_stage_ownership_mode,
            worker_type_definition=worker_type_definition,
        )
        fields_json = fields_codec.fields_to_json(initial_fields)
        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, employee_backend, employee_launch_model, "
            "employee_launch_reasoning_effort, stage, priority, deadline, "
            "project_id, sprint_id, sprint_item_id, "
            "recap, ceiling, at_cap, "
            "ticket_status, stage_ownership_overrides, default_stage_ownership_mode, "
            "conversation_id, alias, fields, created_at, updated_at, "
            "ticket_status_changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, NULL, NULL, "
            "?, ?, ?, ?)",
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
                at_cap.value,
                initial_ticket_status.value,
                "{}",
                (
                    default_stage_ownership_mode.value
                    if default_stage_ownership_mode is not None
                    else None
                ),
                fields_json,
                now,
                now,
                now,
            ),
        )
        if day_id is not None:
            days_data.add_day_ticket(conn, day_id, ticket_id, now)
        for blocker_ticket_id in blocked_by_ticket_ids or []:
            core_links.add_link(conn, blocker_ticket_id, ticket_id, LinkKind.blocks, now)
        return _load_ticket_for_write(conn, ticket_id)


def create_ticket_from_external_work(
    conn: sqlite3.Connection,
    *,
    title: str,
    target_stage: str,
    provided_values: Mapping[str, str],
    actor: str,
    now: int,
    title_max_chars: int,
    kickoff_note: str | None = None,
    recap: str | None = None,
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
) -> Ticket:
    if kickoff_note is None:
        kickoff_note = ""
    admission.validate_title(title, title_max_chars)
    admission.validate_body(kickoff_note, "kickoff note")
    admission.validate_deadline(deadline)
    if recap is not None:
        admission.validate_body(recap, "recap")
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
    # External work is "already done elsewhere": start at the type's FIRST WORKER stage
    # (needs_success / needs_understanding / needs_alpha), NOT the leading needs_kickoff — the
    # applied decision then jumps it to target_stage. first_worker_stage is the concept
    # here; default_ceiling is now needs_kickoff and would wrongly re-park kickoff.
    first_worker = worker_type_definition.first_worker_stage()
    default_stage_ownership_mode = _default_stage_ownership_for_entry(
        conn, worker_type_definition, first_worker
    )
    ticket_id = new_id(ID_PREFIXES["ticket"])
    initial_fields = fields_codec.with_slot(
        TicketFields.empty(worker_type_definition.field_ids()),
        "kickoff",
        FieldSlot(value=kickoff_note),
    )
    with _txn(conn):
        if sprint_item_id is not None and project_id is None and sprint_id is None:
            item = conn.execute(
                "SELECT project_id, sprint_id FROM sprint_items WHERE id = ?",
                (sprint_item_id,),
            ).fetchone()
            if item is not None:
                project_id = str(item["project_id"])
                sprint_id = item["sprint_id"]
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

        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, employee_backend, employee_launch_model, "
            "employee_launch_reasoning_effort, stage, priority, deadline, "
            "project_id, sprint_id, sprint_item_id, "
            "recap, ceiling, at_cap, ticket_status, stage_ownership_overrides, "
            "default_stage_ownership_mode, conversation_id, alias, fields, "
            "created_at, updated_at, ticket_status_changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, NULL, NULL, "
            "?, ?, ?, ?)",
            (
                ticket_id,
                title,
                worker_type,
                launch_configuration.employee_backend,
                launch_configuration.employee_launch_model,
                launch_configuration.employee_launch_reasoning_effort,
                # Start at the type's FIRST WORKER stage. The applied external-work
                # decision then moves it to target_stage; this initial position only needs to be a
                # valid non-terminal worker stage so the pre-persist guard passes
                # (coding: needs_success; probe: needs_alpha). This is NOT default_ceiling,
                # which is now the leading needs_kickoff.
                first_worker,
                stored_priority.value,
                deadline,
                project_id,
                sprint_id,
                sprint_item_id,
                first_worker,
                AtCap.user_review.value,
                TicketStatus.empty.value,
                "{}",
                (
                    default_stage_ownership_mode.value
                    if default_stage_ownership_mode is not None
                    else None
                ),
                fields_codec.fields_to_json(initial_fields),
                now,
                now,
                now,
            ),
        )
        if day_id is not None:
            days_data.add_day_ticket(conn, day_id, ticket_id, now)
        for blocker_ticket_id in blocked_by_ticket_ids or []:
            core_links.add_link(conn, blocker_ticket_id, ticket_id, LinkKind.blocks, now)
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        values_decision, position_decision = external_work.decide_external_work(
            ticket,
            target_stage,
            provided_values,
            worker_type_definition=worker_type_definition,
        )
        ticket = _apply_decision(conn, ticket, values_decision, now)
        if recap is not None:
            conn.execute(
                "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
                (recap, now, ticket_id),
            )
            ticket = _load_ticket_for_write(conn, ticket_id)
        ticket = _apply_decision(conn, ticket, position_decision, now)
        _write_entered_stage_ticket_status(
            conn,
            ticket,
            worker_type_definition=worker_type_definition,
            now=now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def reconcile_ticket_from_external_work(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    target_stage: str,
    provided_values: Mapping[str, str],
    actor: str,
    now: int,
    kickoff_note: str | None = None,
    recap: str | None = None,
) -> Ticket:
    if kickoff_note is None:
        kickoff_note = ""
    admission.validate_body(kickoff_note, "kickoff note")
    if recap is not None:
        admission.validate_body(recap, "recap")
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if ticket.ticket_status not in (
            TicketStatus.empty,
            TicketStatus.blocked,
            TicketStatus.user,
            TicketStatus.paired,
            TicketStatus.errored,
        ):
            raise PlannerError(
                ErrorCode.already_running,
                "ticket control is active",
                {"ticket_id": ticket_id, "ticket_status": ticket.ticket_status.value},
            )

        values_decision, position_decision = external_work.decide_external_work(
            ticket,
            target_stage,
            provided_values,
            worker_type_definition=worker_type_definition,
        )
        ticket = _apply_decision(conn, ticket, values_decision, now)
        if recap is not None and recap != ticket.recap:
            conn.execute(
                "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
                (recap, now, ticket_id),
            )
            ticket = _load_ticket_for_write(conn, ticket_id)
        stage_before_position = ticket.stage
        ticket = _apply_decision(conn, ticket, position_decision, now)
        if ticket.stage != stage_before_position:
            _write_entered_stage_ticket_status(
                conn,
                ticket,
                worker_type_definition=worker_type_definition,
                now=now,
            )
        else:
            _write_resting_ticket_status(
                conn,
                ticket,
                worker_type_definition=worker_type_definition,
                now=now,
            )
        return _load_ticket_for_write(conn, ticket_id)


def audit_ticket_registry_integrity(conn: sqlite3.Connection) -> None:
    """One linear scan over ``tickets`` at boot: every row's ``(worker_type, stage,
    ceiling)`` must be registry-valid and its ``fields`` must decode against the type.

    Uses the configured registry and definition-owned validation plus the field codec,
    so "valid" has one definition and the audit inherits the
    codec's policy verbatim — strict on unknown type / bad stage / bad ceiling /
    missing declared field / malformed slot; lenient on extra top-level keys (a
    legacy ``result`` key does not fail the audit). Scans ``ORDER BY id`` so the
    first corrupt row is deterministic, and raises ``RuntimeError`` naming that
    row's id and the specific reason.

    Invocation is deliberately narrow: this full scan runs once in the ``panels
    serve`` lifespan, after the registry build and before background loops. CLI paths
    do NOT run it — sanctioned writes use validation doors, while the
    production server audits existing rows once before serving or starting background
    work. Plain row loading intentionally returns stored values without resolving a
    Worker type merely to read a Stage."""
    runtime_definitions = configured_worker_runtime_definitions()
    registry = runtime_definitions.worker_type_registry
    for row in conn.execute(
        "SELECT id, worker_type, employee_backend, stage, ceiling, fields FROM tickets ORDER BY id"
    ):
        try:
            require_conversation_backend_key(str(row["employee_backend"]))
            worker_type_definition = registry.require(str(row["worker_type"]))
            worker_type_definition.validate_ticket_position(str(row["stage"]), str(row["ceiling"]))
            fields_codec.declared_fields_from_json(
                str(row["fields"]), worker_type_definition.field_ids()
            )
        except PlannerError as exc:
            raise RuntimeError(
                f"ticket integrity audit failed: id={row['id']} "
                f"reason={exc.message} detail={exc.detail}"
            ) from exc
        except json.JSONDecodeError as exc:
            # Syntactically-invalid fields JSON (the codec's json.loads) must name the
            # offending id too, not abort startup with a raw decode traceback.
            raise RuntimeError(
                f"ticket integrity audit failed: id={row['id']} "
                f"reason=fields JSON is not valid JSON detail={exc}"
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

    Returns the claimed Ticket, whose ``ticket_status`` and ``ticket_status_changed_at``
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
        ownership_mode = machine.effective_stage_ownership_mode(
            ticket.stage,
            ticket.stage_ownership_overrides,
            worker_type_definition=worker_type_definition,
            default_stage_ownership_mode=ticket.default_stage_ownership_mode,
            ceiling=ticket.ceiling,
            at_cap=ticket.at_cap,
        )
        if ownership_mode is None:
            raise PlannerError(
                ErrorCode.validation,
                "a terminal stage has no worker step to claim",
                {"ticket_id": ticket_id, "stage": ticket.stage},
            )
        _write_ticket_status(
            conn,
            ticket_id,
            machine.worker_step_departure_status(ownership_mode),
            now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def release_worker_step_claim(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    expected_status: TicketStatus,
    expected_status_changed_at: int,
    now: int,
) -> bool:
    """Give a worker-step claim back, but only if the Ticket has not moved on since.

    Both halves of the claim have to still match: the departure status AND the moment it
    was written. Comparing the status alone would let a late release erase a later,
    legitimate transition that happened to land on the same status value.

    Reports whether the release actually fired. The flip goes back through the one status
    write door, so a Ticket that has since acquired a live blocker lands on ``blocked``
    rather than ``empty``, exactly as any other return to rest does.
    """
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.ticket_status is not expected_status:
            return False
        if ticket.ticket_status_changed_at != expected_status_changed_at:
            return False
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return True


def mark_ticket_errored(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    error: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        _write_ticket_status(conn, ticket_id, TicketStatus.errored, now, error=error)
        return _load_ticket_for_write(conn, ticket_id)


def file_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    body: str,
    actor: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_file_proposal(
            ticket,
            field,
            body,
            actor,
            now,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        if any(spec.kind is EventKind.proposal_filed for spec in decision.events):
            _write_parked_proposal_status(conn, ticket_id, updated, field, now)
            updated = _load_ticket_for_write(conn, ticket_id)
        else:
            updated = _load_ticket_for_write(conn, ticket_id)
            if decision.new_stage is not None and decision.new_stage != ticket.stage:
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
            updated = _load_ticket_for_write(conn, ticket_id)
        return updated


def file_current_proposal_with_recap(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    recap: str,
    actor: str,
    now: int,
) -> Ticket:
    """Worker proposal surface: infer the current gating field and update recap atomically.

    Standalone recap keeps its normal "past needs_success" guard. A proposal always carries
    a recap, so this writer validates and writes it in the same transaction even when the
    proposal parks at the first success gate.
    """
    admission.validate_body(recap, "recap")
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        field = worker_type_definition.gating_field(ticket.stage)
        if field is None:
            raise PlannerError(
                ErrorCode.validation,
                "ticket stage has no proposal field",
                {"stage": str(ticket.stage)},
            )
        decision = resolution.decide_file_proposal(
            ticket,
            field,
            body,
            actor,
            now,
            worker_type_definition=worker_type_definition,
        )
        _apply_decision(conn, ticket, decision, now)
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
            (recap, now, ticket_id),
        )
        if any(spec.kind is EventKind.proposal_filed for spec in decision.events):
            updated = _load_ticket_for_write(conn, ticket_id)
            _write_parked_proposal_status(conn, ticket_id, updated, field, now)
        else:
            updated = _load_ticket_for_write(conn, ticket_id)
            if decision.new_stage is not None and decision.new_stage != ticket.stage:
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
    actor: str,
    now: int,
    edited_body: str | None = None,
    next_ceiling: NextCeiling | None = None,
    at_cap: AtCap | None = None,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id)
        _require_agent_review_placement(conn, ticket, at_cap, ticket.sprint_item_id)
        decision = resolution.decide_accept(
            ticket,
            field,
            actor,
            edited_body,
            next_ceiling,
            at_cap,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        if edited_body is not None:
            ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        if decision.new_stage is not None and decision.new_stage != ticket.stage:
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


def set_stage_ownership(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    stage: str,
    ownership_mode: StageOwnershipMode | None,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if not worker_type_definition.is_known_stage(stage):
            raise PlannerError(ErrorCode.validation, "invalid stage", {"stage": stage})
        if worker_type_definition.is_terminal(stage):
            raise PlannerError(
                ErrorCode.validation,
                "terminal stage cannot have ownership",
                {"stage": stage},
            )
        effective_before = machine.effective_stage_ownership_mode(
            stage,
            ticket.stage_ownership_overrides,
            worker_type_definition=worker_type_definition,
            default_stage_ownership_mode=(
                ticket.default_stage_ownership_mode
                if stage == ticket.stage
                else _default_stage_ownership_for_entry(conn, worker_type_definition, stage)
            ),
            ceiling=ticket.ceiling,
            at_cap=ticket.at_cap,
        )
        assert effective_before is not None
        overrides = dict(ticket.stage_ownership_overrides)
        if ownership_mode is None:
            overrides.pop(stage, None)
        else:
            overrides[stage] = ownership_mode
        effective_after = machine.effective_stage_ownership_mode(
            stage,
            overrides,
            worker_type_definition=worker_type_definition,
            default_stage_ownership_mode=(
                ticket.default_stage_ownership_mode
                if stage == ticket.stage
                else _default_stage_ownership_for_entry(conn, worker_type_definition, stage)
            ),
            ceiling=ticket.ceiling,
            at_cap=ticket.at_cap,
        )
        if overrides == ticket.stage_ownership_overrides:
            return ticket
        conn.execute(
            "UPDATE tickets SET stage_ownership_overrides = ?, updated_at = ? WHERE id = ?",
            (_stage_ownership_overrides_to_json(overrides), now, ticket_id),
        )
        updated = _load_ticket_for_write(conn, ticket_id)
        assert effective_after is not None
        if (
            stage == ticket.stage
            and effective_before is not effective_after
            and updated.ticket_status
            not in (
                TicketStatus.agent,
                TicketStatus.awaiting_agent_review,
                TicketStatus.awaiting_user_review,
                TicketStatus.errored,
            )
        ):
            _write_entered_stage_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
            updated = _load_ticket_for_write(conn, ticket_id)
        return updated


def take_over_ticket(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if ticket.stage in ("done", "dropped"):
            raise PlannerError(
                ErrorCode.validation,
                "terminal tickets cannot be taken over",
                {"ticket_id": ticket_id, "stage": ticket.stage},
            )
        if ticket.stage == "needs_kickoff":
            raise PlannerError(
                ErrorCode.validation,
                "kickoff must be settled before takeover",
                {"ticket_id": ticket_id},
            )
        if ticket.stage_ownership_overrides.get(ticket.stage) is StageOwnershipMode.user:
            return ticket
        effective_before = ticket.effective_stage_ownership_mode
        overrides = dict(ticket.stage_ownership_overrides)
        overrides[ticket.stage] = StageOwnershipMode.user
        conn.execute(
            "UPDATE tickets SET stage_ownership_overrides = ?, updated_at = ? WHERE id = ?",
            (_stage_ownership_overrides_to_json(overrides), now, ticket_id),
        )
        updated = _load_ticket_for_write(conn, ticket_id)
        if effective_before is not StageOwnershipMode.user and updated.ticket_status not in (
            TicketStatus.agent,
            TicketStatus.awaiting_agent_review,
            TicketStatus.awaiting_user_review,
            TicketStatus.errored,
        ):
            _write_entered_stage_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
            updated = _load_ticket_for_write(conn, ticket_id)
        return updated


def release_ticket(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if ticket.stage in ("done", "dropped"):
            raise PlannerError(
                ErrorCode.validation,
                "terminal tickets cannot be released",
                {"ticket_id": ticket_id, "stage": ticket.stage},
            )
        if ticket.stage == "needs_kickoff":
            raise PlannerError(
                ErrorCode.validation,
                "kickoff must be settled before release",
                {"ticket_id": ticket_id},
            )
        if (
            ticket.ticket_status is TicketStatus.needs_user
            and ticket.stage not in ticket.stage_ownership_overrides
        ):
            _write_entered_stage_ticket_status(
                conn,
                ticket,
                worker_type_definition=worker_type_definition,
                now=now,
            )
            return _load_ticket_for_write(conn, ticket_id)
        if ticket.stage not in ticket.stage_ownership_overrides:
            return ticket
        effective_before = ticket.effective_stage_ownership_mode
        overrides = dict(ticket.stage_ownership_overrides)
        overrides.pop(ticket.stage, None)
        effective_after = machine.effective_stage_ownership_mode(
            ticket.stage,
            overrides,
            worker_type_definition=worker_type_definition,
            default_stage_ownership_mode=ticket.default_stage_ownership_mode,
            ceiling=ticket.ceiling,
            at_cap=ticket.at_cap,
        )
        conn.execute(
            "UPDATE tickets SET stage_ownership_overrides = ?, updated_at = ? WHERE id = ?",
            (_stage_ownership_overrides_to_json(overrides), now, ticket_id),
        )
        updated = _load_ticket_for_write(conn, ticket_id)
        if effective_before is not effective_after and updated.ticket_status not in (
            TicketStatus.agent,
            TicketStatus.awaiting_agent_review,
            TicketStatus.awaiting_user_review,
            TicketStatus.errored,
        ):
            _write_entered_stage_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
            updated = _load_ticket_for_write(conn, ticket_id)
        return updated


def request_user_help(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket:
    """Pause a Worker-owned Ticket for explicit human help."""
    admission.require_worker_actor(actor, "request user help")
    with _txn(conn):
        ticket, _worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if ticket.stage in ("done", "dropped"):
            raise PlannerError(
                ErrorCode.validation,
                "terminal tickets cannot request user help",
                {"ticket_id": ticket_id},
            )
        if ticket.stage == "needs_kickoff":
            raise PlannerError(
                ErrorCode.validation,
                "kickoff must be settled before requesting user help",
                {"ticket_id": ticket_id},
            )
        if ticket.ticket_status is not TicketStatus.needs_user:
            _write_ticket_status(conn, ticket_id, TicketStatus.needs_user, now)
        return _load_ticket_for_write(conn, ticket_id)


def enter_paired_on_human_reply(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    """Flip a filed proposal to paired when a human replies with a typed message.

    Automatic consequence of message admission, not an actor-authored write, so no actor is
    required. A no-op unless the Ticket is parked for user review.
    """
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.ticket_status is TicketStatus.awaiting_user_review:
            _write_ticket_status(conn, ticket_id, TicketStatus.paired, now)
        return _load_ticket_for_write(conn, ticket_id)


def edit_field_value(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    new_body: str,
    actor: str,
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
            actor,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return updated


def return_for_revision(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    actor: str,
    now: int,
    expected_proposal: Proposal | None = None,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    admission.validate_body(message, "revision guidance")
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id)
        field = worker_type_definition.gating_field(ticket.stage)
        current_proposal = (
            fields_codec.get_slot(ticket.fields, field).proposal if field is not None else None
        )
        if expected_proposal is not None and current_proposal != expected_proposal:
            raise PlannerError(
                ErrorCode.validation,
                "proposal changed before revision guidance was applied",
                {"ticket_id": ticket_id, "field": field},
            )
        decision = resolution.decide_return_for_revision(
            ticket,
            actor,
            worker_type_definition=worker_type_definition,
        )
        _apply_decision(conn, ticket, decision, now)
        _write_ticket_status(conn, ticket_id, TicketStatus.agent, now)
        return _load_ticket_for_write(conn, ticket_id)


def set_stage(
    conn: sqlite3.Connection, ticket_id: str, *, new_stage: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        worker_type_definition.validate_ticket_position(new_stage, ticket.ceiling)
        # decide_stage_jump guards only on the universal bookends + the equality check,
        # so it needs no definition; the ingress validated new_stage against the type,
        # and _apply_decision re-validates the prospective (stage, ceiling) per-type.
        decision = resolution.decide_stage_jump(ticket, new_stage, actor)
        updated = _apply_decision(conn, ticket, decision, now)
        _write_entered_stage_ticket_status(
            conn,
            updated,
            worker_type_definition=worker_type_definition,
            now=now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def drop_ticket(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_drop(ticket, actor)
        updated = _apply_decision(conn, ticket, decision, now)
        _write_resting_ticket_status(
            conn,
            updated,
            worker_type_definition=worker_type_definition,
            now=now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def delete_ticket(
    conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int
) -> TicketDeletion:
    """Permanently remove a mistaken ticket and its product footprint in one transaction.

    Deletion is blocked while the Ticket's status says a worker step is out. Whether the
    Ticket's conversation is live is a question for the conversation system, so the route
    asks it before calling this writer; this writer stays a pure database transaction.
    """
    admission.require_direct_actor(actor, "delete_ticket")
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.ticket_status is TicketStatus.agent:
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
        link_rows = conn.execute(
            "SELECT from_id, to_id, kind FROM links "
            "WHERE from_id = ? OR to_id = ? ORDER BY from_id, to_id, kind",
            (ticket_id, ticket_id),
        ).fetchall()
        linked_entity_ids = tuple(
            sorted(
                {
                    str(row["to_id"] if row["from_id"] == ticket_id else row["from_id"])
                    for row in link_rows
                }
            )
        )
        sprint_item_ids = (ticket.sprint_item_id,) if ticket.sprint_item_id is not None else ()
        effective_sprint_id = ticket.effective_sprint_id
        sprint_ids = (effective_sprint_id,) if effective_sprint_id is not None else ()

        conn.execute(
            "DELETE FROM pending_worker_context WHERE worker_entity_id = ?",
            (ticket_id,),
        )

        for day_id in day_ids:
            days_data.remove_day_ticket(conn, day_id, ticket_id, now)
        for row in link_rows:
            from_id = str(row["from_id"])
            to_id = str(row["to_id"])
            kind = str(row["kind"])
            conn.execute(
                "DELETE FROM links WHERE from_id = ? AND to_id = ? AND kind = ?",
                (from_id, to_id, kind),
            )
        # The deleted Ticket held those blocks links; every target it named re-derives
        # its stand-in now that they are gone.
        for row in link_rows:
            if str(row["from_id"]) == ticket_id and str(row["kind"]) == LinkKind.blocks.value:
                settle_blocked_standin_for_link_target(conn, str(row["to_id"]), now)

        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        return TicketDeletion(
            ticket_id=ticket_id,
            title=ticket.title,
            day_ids=day_ids,
            sprint_item_ids=sprint_item_ids,
            sprint_ids=sprint_ids,
            linked_entity_ids=linked_entity_ids,
        )


def change_scope(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    ceiling: str,
    at_cap: AtCap,
    actor: str,
    now: int,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    if (
        actor == admission.SPRINT_ITEM_SUPERVISOR_ACTOR
        and supervisor_sprint_item_id is None
    ):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "change_scope requires the Sprint Item supervisor parent",
            {"actor": actor, "ticket_id": ticket_id},
        )
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id)
        _require_agent_review_placement(conn, ticket, at_cap, ticket.sprint_item_id)
        decision = resolution.decide_scope_change(
            ticket,
            ceiling,
            at_cap,
            actor,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        parked_field = worker_type_definition.gating_field(updated.stage)
        if ticket.ticket_status in {
            TicketStatus.awaiting_agent_review,
            TicketStatus.awaiting_user_review,
        } and (
            parked_field is not None
            and fields_codec.get_slot(updated.fields, parked_field).proposal is not None
        ):
            # The proposal already in front of a reviewer follows the new scope. This is
            # how a supervisor hands review to the user.
            _write_parked_proposal_status(conn, ticket_id, updated, parked_field, now)
        elif ticket.ticket_status in {
            TicketStatus.empty,
            TicketStatus.blocked,
            TicketStatus.paired,
            TicketStatus.user,
        }:
            _write_resting_ticket_status(
                conn,
                updated,
                worker_type_definition=worker_type_definition,
                now=now,
            )
        return _load_ticket_for_write(conn, ticket_id)


def replace_field_user_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    user_note: str | None,
    actor: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if not worker_type_definition.has_field(field):
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": str(field)})
        slot = fields_codec.get_slot(ticket.fields, str(field))
        new_slot = FieldSlot(value=slot.value, proposal=slot.proposal, user_note=user_note)
        new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
        conn.execute(
            "UPDATE tickets SET fields = ?, updated_at = ? WHERE id = ?",
            (fields_codec.fields_to_json(new_fields), now, ticket_id),
        )
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


def append_field_user_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    user_note: str,
    actor: str,
    now: int,
) -> Ticket:
    """Append field user guidance without replacing the existing note.

    Empty appends are no-ops. Non-empty notes use one blank line as the separator.
    The read and write stay in one transaction so concurrent append callers do not
    lose a note that they read before their own write.
    """
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        if not worker_type_definition.has_field(field):
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": str(field)})
        slot = fields_codec.get_slot(ticket.fields, str(field))
        if not user_note:
            return ticket
        existing_note = slot.user_note
        combined_note = user_note if not existing_note else f"{existing_note}\n\n{user_note}"
        new_slot = FieldSlot(value=slot.value, proposal=slot.proposal, user_note=combined_note)
        new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
        conn.execute(
            "UPDATE tickets SET fields = ?, updated_at = ? WHERE id = ?",
            (fields_codec.fields_to_json(new_fields), now, ticket_id),
        )
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


def replace_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    note: str | None,
    actor: str,
    now: int,
) -> Ticket:
    return replace_field_user_note(
        conn, ticket_id, field=field, user_note=note, actor=actor, now=now
    )


def append_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    note: str,
    actor: str,
    now: int,
) -> Ticket:
    return append_field_user_note(
        conn, ticket_id, field=field, user_note=note, actor=actor, now=now
    )


# Backwards-compatible names for existing data-layer callers.
def set_field_user_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    user_note: str | None,
    actor: str,
    now: int,
) -> Ticket:
    return replace_field_user_note(
        conn, ticket_id, field=field, user_note=user_note, actor=actor, now=now
    )


def set_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    note: str | None,
    actor: str,
    now: int,
) -> Ticket:
    return replace_note(conn, ticket_id, field=field, note=note, actor=actor, now=now)


def edit_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    edit: TicketEdit,
    title_max_chars: int,
    actor: str,
    now: int,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    if (
        actor == admission.SPRINT_ITEM_SUPERVISOR_ACTOR
        and supervisor_sprint_item_id is None
    ):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "edit_ticket requires the Sprint Item supervisor parent",
            {"actor": actor, "ticket_id": ticket_id},
        )
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        _require_current_supervisor_parent(conn, ticket, supervisor_sprint_item_id)

        title = edit["title"] if "title" in edit else ticket.title
        priority = edit["priority"] if "priority" in edit else ticket.priority
        deadline = edit["deadline"] if "deadline" in edit else ticket.deadline

        project_id = edit["project_id"] if "project_id" in edit else ticket.project_id
        sprint_id = edit["sprint_id"] if "sprint_id" in edit else ticket.sprint_id
        sprint_item_id = (
            edit["sprint_item_id"] if "sprint_item_id" in edit else ticket.sprint_item_id
        )
        _require_agent_review_placement(conn, ticket, ticket.at_cap, sprint_item_id)

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
        candidates: tuple[tuple[str, str, str | None, str | None], ...] = (
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
        if any(field == "sprint_item_id" for field, *_rest in changes):
            from planner.supervisor_obligations.data import project_ticket

            project_ticket(conn, ticket_id, now)
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


def write_recap(
    conn: sqlite3.Connection, ticket_id: str, *, body: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
            (body, now, ticket_id),
        )
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


def move_ticket_to_sprint_item(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    sprint_item_id: str,
    actor: str,
    now: int,
    admit: Callable[[], None] | None = None,
) -> Ticket:
    with _txn(conn):
        if admit is not None:
            admit()
        ticket = _load_ticket_for_write(conn, ticket_id)
        item = conn.execute(
            "SELECT project_id, sprint_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is None:
            raise PlannerError(
                ErrorCode.not_found,
                "sprint item not found",
                {"sprint_item_id": sprint_item_id},
            )
        if ticket.sprint_item_id == sprint_item_id:
            return ticket
        _require_agent_review_placement(conn, ticket, ticket.at_cap, sprint_item_id)
        conn.execute(
            "UPDATE tickets SET sprint_item_id = ?, project_id = ?, sprint_id = ?, "
            "updated_at = ? WHERE id = ?",
            (sprint_item_id, str(item["project_id"]), item["sprint_id"], now, ticket_id),
        )
        from planner.supervisor_obligations.data import project_ticket

        project_ticket(conn, ticket_id, now)
        ticket_worker_context.set_ticket_placement_changed(conn, ticket_id)
        return _load_ticket_for_write(conn, ticket_id)


def move_ticket_to_backlog(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    sprint_item_id: str,
    actor: str,
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
        if ticket.sprint_item_id != sprint_item_id:
            return ticket
        _require_agent_review_placement(conn, ticket, ticket.at_cap, None)
        conn.execute(
            "UPDATE tickets SET sprint_item_id = NULL, sprint_id = NULL, project_id = ?, "
            "updated_at = ? WHERE id = ?",
            (str(item["project_id"]), now, ticket_id),
        )
        ticket_worker_context.set_ticket_placement_changed(conn, ticket_id)
        return _load_ticket_for_write(conn, ticket_id)
