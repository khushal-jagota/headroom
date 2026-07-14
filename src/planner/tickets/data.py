"""The only module that writes Ticket rows. Stage, ceiling/at_cap, and fields
value mutations happen in exactly one function (_apply_decision); every public
writer is one BEGIN IMMEDIATE transaction. An ordinary Ticket edit validates and
writes its requested plain attributes together. Other semantic writers remain
separate. sqlite3, events and ids live here only; the clock arrives as now (unix
seconds) and the title limit as an argument."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Final, Protocol

from planner.core import links as core_links
from planner.core.contracts import EventKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event, delete_entity_history
from planner.core.ids import ID_PREFIXES, new_id
from planner.days import data as days_data
from planner.tickets import worker_context as ticket_worker_context
from planner.tickets.contracts import (
    AtCap,
    FieldSlot,
    Implementer,
    NextCeiling,
    Proposal,
    Ticket,
    TicketDeletion,
    TicketEdit,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import (
    admission,
    external_work,
    fields_codec,
    machine,
    resolution,
)
from planner.tickets.logic.decisions import Decision
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition


class _Unset:
    """Typed sentinel for status helpers: leave chat_session_key untouched."""


_UNSET: Final = _Unset()


class _AutomaticEmployeeStepEligibilityCheck(Protocol):
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
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    worker_type = str(row["worker_type"])
    return Ticket(
        id=row["id"],
        title=row["title"],
        stage=str(row["stage"]),
        priority=Priority(row["priority"]),
        deadline=row["deadline"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        sprint_item_id=row["sprint_item_id"],
        sprint_id=row["sprint_id"],
        recap=row["recap"],
        ceiling=str(row["ceiling"]),
        at_cap=AtCap(row["at_cap"]),
        ticket_status=TicketStatus(row["ticket_status"]),
        implementer=Implementer(row["implementer"]) if row["implementer"] is not None else None,
        chat_session_key=row["chat_session_key"],
        alias=row["alias"],
        fields=fields_codec.fields_from_json(row["fields"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        worker_type=worker_type,
    )


def _ticket_row(conn: sqlite3.Connection, ticket_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT tickets.*, projects.name AS project_name "
        "FROM tickets LEFT JOIN projects ON projects.id = tickets.project_id "
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


def _active_blocker_stage(stage: str) -> bool:
    return stage not in {"done", "dropped"}


def _outgoing_block_target_ids(conn: sqlite3.Connection, ticket_id: str) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT to_id FROM links WHERE from_id = ? AND kind = 'blocks' ORDER BY to_id",
        (ticket_id,),
    ).fetchall()
    return tuple(str(row["to_id"]) for row in rows)


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
        "UPDATE tickets SET fields = ?, stage = ?, ceiling = ?, at_cap = ?, updated_at = ? "
        "WHERE id = ?",
        (
            fields_codec.fields_to_json(new_fields),
            str(new_stage),
            str(new_ceiling),
            new_at_cap.value,
            now,
            ticket.id,
        ),
    )
    for spec in decision.events:
        payload = spec.payload
        if spec.kind is EventKind.stage_changed and affected_blocked_target_ids:
            payload = {
                **payload,
                "affected_blocked_target_ids": list(affected_blocked_target_ids),
            }
        append_event(conn, ticket.id, spec.kind, payload, now)
    if any(spec.kind is EventKind.stage_changed for spec in decision.events):
        _append_item_children_changed(conn, ticket.sprint_item_id, ticket.id, "stage", now)
    return _load_ticket(conn, ticket.id)


def _append_item_children_changed(
    conn: sqlite3.Connection,
    sprint_item_id: str | None,
    ticket_id: str,
    reason: str,
    now: int,
) -> None:
    if sprint_item_id is None:
        return
    append_event(
        conn,
        sprint_item_id,
        EventKind.item_children_changed,
        {"ticket_id": ticket_id, "reason": reason},
        now,
    )


def _write_ticket_status(
    conn: sqlite3.Connection,
    ticket_id: str,
    ticket_status: TicketStatus,
    now: int,
    *,
    error: str | None = None,
) -> None:
    row = conn.execute("SELECT sprint_item_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.execute(
        "UPDATE tickets SET ticket_status = ?, updated_at = ? WHERE id = ?",
        (ticket_status.value, now, ticket_id),
    )
    payload: dict[str, object] = {"ticket_status": ticket_status.value}
    if error is not None:
        payload["error"] = error
    append_event(conn, ticket_id, EventKind.ticket_status_changed, payload, now)
    _append_item_children_changed(
        conn,
        str(row["sprint_item_id"])
        if row is not None and row["sprint_item_id"] is not None
        else None,
        ticket_id,
        "ticket_status",
        now,
    )


def _persist_ticket_chat_session_key(
    conn: sqlite3.Connection,
    ticket_id: str,
    session_key: str | None | _Unset,
    now: int,
) -> None:
    if isinstance(session_key, _Unset):
        return
    conn.execute(
        "UPDATE tickets SET chat_session_key = ?, updated_at = ? WHERE id = ?",
        (session_key, now, ticket_id),
    )


def claim_running_step_chat_session_key(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    session_key: str,
    now: int,
) -> Ticket:
    """Claim the durable Hermes session key for the active worker step."""
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.chat_session_key == session_key:
            return ticket
        if ticket.ticket_status is not TicketStatus.agent_running_step:
            return ticket
        _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
        append_event(
            conn,
            ticket_id,
            EventKind.chat_session_created,
            {"session_key": session_key},
            now,
        )
        return _load_ticket_for_write(conn, ticket_id)


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
    now: int,
    title_max_chars: int,
    kickoff_note: str = "",
    project_id: str | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
    implementer: Implementer | None = None,
    worker_type: str,
) -> Ticket:
    admission.validate_title(title, title_max_chars)
    admission.validate_deadline(deadline)
    worker_type_definition = configured_worker_type_registry().require(worker_type)
    initial_stage = worker_type_definition.default_ceiling()
    default_ceiling = worker_type_definition.default_ceiling()
    ticket_id = new_id(ID_PREFIXES["ticket"])
    initial_fields = fields_codec.with_slot(
        TicketFields.empty(worker_type_definition.field_ids()),
        "kickoff",
        FieldSlot(
            value=None,
            proposal=Proposal(body=kickoff_note, proposed_by=actor, created_at=now),
            user_note=None,
        ),
    )
    fields_json = fields_codec.fields_to_json(initial_fields)
    with _txn(conn):
        if sprint_item_id is not None:
            if (
                conn.execute(
                    "SELECT 1 FROM sprint_items WHERE id = ?", (sprint_item_id,)
                ).fetchone()
                is None
            ):
                raise PlannerError(
                    ErrorCode.not_found, "sprint item not found", {"sprint_item_id": sprint_item_id}
                )
            if sprint_id is not None:
                raise PlannerError(
                    ErrorCode.sprint_derived,
                    "sprint_id is derived from the parent item",
                    {"sprint_item_id": sprint_item_id},
                )
            if project_id is not None:
                raise PlannerError(ErrorCode.validation, "project is derived when parented")
        elif sprint_id is not None:
            exists = conn.execute("SELECT 1 FROM sprints WHERE id = ?", (sprint_id,)).fetchone()
            if exists is None:
                raise PlannerError(
                    ErrorCode.not_found, "sprint not found", {"sprint_id": sprint_id}
                )
        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, stage, priority, deadline, project_id, sprint_item_id, "
            "sprint_id, recap, ceiling, at_cap, "
            "ticket_status, implementer, "
            "chat_session_key, alias, fields, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
            (
                ticket_id,
                title,
                worker_type,
                initial_stage,
                priority.value,
                deadline,
                project_id,
                sprint_item_id,
                sprint_id,
                default_ceiling,
                AtCap.propose.value,
                TicketStatus.awaiting_approval.value,
                implementer.value if implementer is not None else None,
                fields_json,
                now,
                now,
            ),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_created,
            {"stage": initial_stage},
            now,
        )
        append_event(
            conn,
            ticket_id,
            EventKind.proposal_filed,
            {"field": "kickoff", "body": kickoff_note, "proposed_by": actor},
            now,
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_status_changed,
            {"ticket_status": TicketStatus.awaiting_approval.value},
            now,
        )
        _append_item_children_changed(conn, sprint_item_id, ticket_id, "created", now)
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
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
    worker_type: str,
) -> Ticket:
    if kickoff_note is None:
        kickoff_note = ""
    admission.validate_title(title, title_max_chars)
    admission.validate_body(kickoff_note, "kickoff note")
    admission.validate_deadline(deadline)
    if recap is not None:
        admission.validate_body(recap, "recap")
    worker_type_definition = configured_worker_type_registry().require(worker_type)
    # External work is "already done elsewhere": seed at the type's FIRST WORKER stage
    # (needs_success / needs_stages / needs_alpha), NOT the leading needs_kickoff — the
    # applied decision then jumps it to target_stage. first_worker_stage is the concept
    # here; default_ceiling is now needs_kickoff and would wrongly re-park kickoff.
    first_worker = worker_type_definition.first_worker_stage()
    ticket_id = new_id(ID_PREFIXES["ticket"])
    initial_fields = fields_codec.with_slot(
        TicketFields.empty(worker_type_definition.field_ids()),
        "kickoff",
        FieldSlot(value=kickoff_note),
    )
    with _txn(conn):
        if sprint_item_id is not None:
            if (
                conn.execute(
                    "SELECT 1 FROM sprint_items WHERE id = ?", (sprint_item_id,)
                ).fetchone()
                is None
            ):
                raise PlannerError(
                    ErrorCode.not_found,
                    "sprint item not found",
                    {"sprint_item_id": sprint_item_id},
                )
            if sprint_id is not None:
                raise PlannerError(
                    ErrorCode.sprint_derived,
                    "sprint_id is derived from the parent item",
                    {"sprint_item_id": sprint_item_id},
                )
            if project_id is not None:
                raise PlannerError(ErrorCode.validation, "project is derived when parented")
        elif (
            sprint_id is not None
            and conn.execute("SELECT 1 FROM sprints WHERE id = ?", (sprint_id,)).fetchone() is None
        ):
            raise PlannerError(ErrorCode.not_found, "sprint not found", {"sprint_id": sprint_id})

        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, stage, priority, deadline, project_id, sprint_item_id, "
            "sprint_id, recap, ceiling, at_cap, ticket_status, "
            "chat_session_key, alias, fields, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, NULL, NULL, ?, ?, ?)",
            (
                ticket_id,
                title,
                worker_type,
                # Seed at the type's FIRST WORKER stage. The applied external-work
                # decision then moves it to target_stage; the seed only needs to be a
                # valid non-terminal worker stage so the pre-persist guard passes
                # (coding: needs_success; probe: needs_alpha). This is NOT default_ceiling,
                # which is now the leading needs_kickoff.
                first_worker,
                priority.value,
                deadline,
                project_id,
                sprint_item_id,
                sprint_id,
                first_worker,
                AtCap.propose.value,
                TicketStatus.empty.value,
                fields_codec.fields_to_json(initial_fields),
                now,
                now,
            ),
        )
        append_event(conn, ticket_id, EventKind.ticket_created, {"stage": first_worker}, now)
        _append_item_children_changed(conn, sprint_item_id, ticket_id, "created", now)
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
            append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
            ticket = _load_ticket_for_write(conn, ticket_id)
        ticket = _apply_decision(conn, ticket, position_decision, now)
        return ticket


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
        if ticket.ticket_status not in (TicketStatus.empty, TicketStatus.errored):
            raise PlannerError(
                ErrorCode.already_running,
                "ticket control is active",
                {"ticket_id": ticket_id, "ticket_status": ticket.ticket_status.value},
            )
        running_turn = conn.execute(
            "SELECT 1 FROM chat_turns WHERE entity_id = ? AND status = 'running' LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if running_turn is not None:
            raise PlannerError(
                ErrorCode.already_running,
                "ticket chat turn is running",
                {"ticket_id": ticket_id},
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
            append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
            ticket = _load_ticket_for_write(conn, ticket_id)
        ticket = _apply_decision(conn, ticket, position_decision, now)
        if ticket.ticket_status is TicketStatus.errored:
            _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
            ticket = _load_ticket_for_write(conn, ticket_id)
        return ticket


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
    serve`` lifespan, after the registry build and before background loops. Seed and
    CLI paths do NOT run it — sanctioned writes use validation doors, while the
    production server audits existing rows once before serving or starting background
    work. Plain row loading intentionally returns stored values without resolving a
    Worker type merely to read a Stage."""
    registry = configured_worker_type_registry()
    for row in conn.execute(
        "SELECT id, worker_type, stage, ceiling, fields FROM tickets ORDER BY id"
    ):
        try:
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


def read_ticket_by_session_key(conn: sqlite3.Connection, session_key: str) -> Ticket:
    """The ticket whose durable chat_session_key matches — how a worker agent resolves
    'my ticket' from its live HERMES_SESSION_KEY (the shared child binds it per turn)."""
    row = conn.execute(
        "SELECT tickets.*, projects.name AS project_name "
        "FROM tickets LEFT JOIN projects ON projects.id = tickets.project_id "
        "WHERE tickets.chat_session_key = ?",
        (session_key,),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.not_found, "no ticket owns this session", {"session_key": session_key}
        )
    return _row_to_ticket(row)


def get_effective_sprint_id(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    ticket = _load_ticket(conn, ticket_id)
    if ticket.sprint_item_id is None:
        return ticket.sprint_id
    row = conn.execute(
        "SELECT sprint_id FROM sprint_items WHERE id = ?", (ticket.sprint_item_id,)
    ).fetchone()
    if row is None:
        return None
    sprint_id: str | None = row["sprint_id"]
    return sprint_id


def claim_automatic_employee_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    planning_day_id_resolver: Callable[[], str],
    eligibility_check: _AutomaticEmployeeStepEligibilityCheck,
    now: int,
) -> Ticket | None:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        planning_day_id = planning_day_id_resolver()
        if not eligibility_check(
            conn,
            ticket,
            planning_day_id=planning_day_id,
            worker_type_definition=worker_type_definition,
        ):
            return None
        _write_ticket_status(conn, ticket_id, TicketStatus.agent_running_step, now)
        return _load_ticket_for_write(conn, ticket_id)


def finish_run_if_still_running_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    session_key: str | None | _Unset = _UNSET,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
        if ticket.ticket_status is TicketStatus.agent_running_step:
            _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return _load_ticket_for_write(conn, ticket_id)


def mark_run_errored(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    error: str,
    session_key: str | None | _Unset = _UNSET,
    now: int,
) -> Ticket:
    with _txn(conn):
        _load_ticket_for_write(conn, ticket_id)
        _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
        _write_ticket_status(conn, ticket_id, TicketStatus.errored, now, error=error)
        return _load_ticket_for_write(conn, ticket_id)


def mark_run_errored_if_still_running_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    error: str,
    session_key: str | None | _Unset = _UNSET,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.ticket_status is TicketStatus.agent_running_step:
            _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
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
            _write_ticket_status(conn, ticket_id, TicketStatus.awaiting_approval, now)
            updated = _load_ticket_for_write(conn, ticket_id)
        else:
            handoff_status = machine.plan_handoff_status(
                ticket.implementer,
                ticket.stage,
                decision.new_stage,
                worker_type_definition=worker_type_definition,
            )
            if handoff_status is not None:
                _write_ticket_status(conn, ticket_id, handoff_status, now)
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
        append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
        if any(spec.kind is EventKind.proposal_filed for spec in decision.events):
            _write_ticket_status(conn, ticket_id, TicketStatus.awaiting_approval, now)
        else:
            handoff_status = machine.plan_handoff_status(
                ticket.implementer,
                ticket.stage,
                decision.new_stage,
                worker_type_definition=worker_type_definition,
            )
            if handoff_status is not None:
                _write_ticket_status(conn, ticket_id, handoff_status, now)
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
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_accept(
            ticket,
            field,
            actor,
            edited_body,
            next_ceiling,
            at_cap,
            worker_type_definition=worker_type_definition,
        )
        _apply_decision(conn, ticket, decision, now)
        if edited_body is not None:
            ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        handoff_status = machine.plan_handoff_status(
            ticket.implementer,
            ticket.stage,
            decision.new_stage,
            worker_type_definition=worker_type_definition,
        )
        _write_ticket_status(conn, ticket_id, handoff_status or TicketStatus.empty, now)
        return _load_ticket_for_write(conn, ticket_id)


def take_over_ticket(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.stage == "needs_kickoff":
            raise PlannerError(
                ErrorCode.validation,
                "kickoff must be settled before takeover",
                {"ticket_id": ticket_id},
            )
        _write_ticket_status(conn, ticket_id, TicketStatus.user_takeover, now)
        return _load_ticket_for_write(conn, ticket_id)


def release_ticket(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if ticket.stage == "needs_kickoff":
            raise PlannerError(
                ErrorCode.validation,
                "kickoff must be settled before release",
                {"ticket_id": ticket_id},
            )
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
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
) -> Ticket:
    admission.validate_body(message, "revision guidance")
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_return_for_revision(
            ticket,
            actor,
            worker_type_definition=worker_type_definition,
        )
        running_turn = conn.execute(
            "SELECT 1 FROM chat_turns WHERE entity_id = ? AND status = 'running' LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if running_turn is not None:
            raise PlannerError(
                ErrorCode.already_running,
                "ticket chat turn is running",
                {"ticket_id": ticket_id},
            )
        _apply_decision(conn, ticket, decision, now)
        _write_ticket_status(conn, ticket_id, TicketStatus.agent_running_step, now)
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
        return _apply_decision(conn, ticket, decision, now)


def drop_ticket(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        decision = resolution.decide_drop(ticket, actor)
        return _apply_decision(conn, ticket, decision, now)


def delete_ticket(
    conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int
) -> TicketDeletion:
    """Permanently remove a mistaken ticket and its product footprint in one transaction.

    Deletion is blocked while durable ticket control owns a worker step or any
    product-visible turn is still running. Both are checked under the same write lock
    before cleanup. All ticket-owned Planner history is removed; the one surviving
    ticket event is the minimal deletion audit and invalidation doorbell.
    """
    admission.require_direct_actor(actor, "delete_ticket")
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        running_turn = conn.execute(
            "SELECT 1 FROM chat_turns WHERE entity_id = ? AND status = 'running' LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if ticket.ticket_status is TicketStatus.agent_running_step or running_turn is not None:
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
        effective_sprint_id = ticket.sprint_id
        if ticket.sprint_item_id is not None:
            item_row = conn.execute(
                "SELECT sprint_id FROM sprint_items WHERE id = ?", (ticket.sprint_item_id,)
            ).fetchone()
            if item_row is not None and item_row["sprint_id"] is not None:
                effective_sprint_id = str(item_row["sprint_id"])
        sprint_ids = (effective_sprint_id,) if effective_sprint_id is not None else ()

        # Prune only pre-delete history. The cleanup events written below stay as
        # doorbells on the surviving day, item, and link endpoints.
        delete_entity_history(conn, ticket_id)

        turn_ids = tuple(
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM chat_turns WHERE entity_id = ?", (ticket_id,)
            ).fetchall()
        )
        if turn_ids:
            placeholders = ",".join("?" for _ in turn_ids)
            conn.execute(
                f"DELETE FROM chat_messages WHERE entity_id = ? OR turn_id IN ({placeholders})",
                (ticket_id, *turn_ids),
            )
        else:
            conn.execute("DELETE FROM chat_messages WHERE entity_id = ?", (ticket_id,))
        conn.execute("DELETE FROM chat_turns WHERE entity_id = ?", (ticket_id,))
        conn.execute("DELETE FROM pending_worker_context WHERE worker_entity_id = ?", (ticket_id,))

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
            survivor_id = to_id if from_id == ticket_id else from_id
            append_event(
                conn,
                survivor_id,
                EventKind.link_removed,
                {"from_id": from_id, "to_id": to_id, "kind": kind},
                now,
            )

        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        for sprint_item_id in sprint_item_ids:
            _append_item_children_changed(conn, sprint_item_id, ticket_id, "deleted", now)
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_deleted,
            {
                "ticket_id": ticket_id,
                "title": ticket.title,
                "actor": actor,
            },
            now,
        )
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
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        decision = resolution.decide_scope_change(
            ticket,
            ceiling,
            at_cap,
            actor,
            worker_type_definition=worker_type_definition,
        )
        updated = _apply_decision(conn, ticket, decision, now)
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return updated


def set_field_user_note(
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
        append_event(conn, ticket_id, EventKind.note_updated, {"field": str(field)}, now)
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


# Backwards-compatible name for the legacy /notes route and worker note command.
def set_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    note: str | None,
    actor: str,
    now: int,
) -> Ticket:
    return set_field_user_note(conn, ticket_id, field=field, user_note=note, actor=actor, now=now)


def edit_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    edit: TicketEdit,
    title_max_chars: int,
    actor: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)

        title = edit["title"] if "title" in edit else ticket.title
        priority = edit["priority"] if "priority" in edit else ticket.priority
        deadline = edit["deadline"] if "deadline" in edit else ticket.deadline
        implementer = edit["implementer"] if "implementer" in edit else ticket.implementer
        project_id = edit["project_id"] if "project_id" in edit else ticket.project_id
        sprint_id = edit["sprint_id"] if "sprint_id" in edit else ticket.sprint_id

        # Validate the intended final Ticket before its first durable effect. Parent
        # restrictions use request-key presence: explicitly assigning the same/null
        # derived value is still an attempted edit and retains the existing error.
        admission.validate_title(title, title_max_chars)
        admission.validate_deadline(deadline)
        if "project_id" in edit and ticket.sprint_item_id is not None:
            raise PlannerError(ErrorCode.validation, "project is derived when parented")
        if (
            project_id is not None
            and conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
            is None
        ):
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
        if "sprint_id" in edit:
            admission.check_sprint_assignable(ticket_id, ticket.sprint_item_id)
        if (
            sprint_id is not None
            and conn.execute("SELECT 1 FROM sprints WHERE id = ?", (sprint_id,)).fetchone() is None
        ):
            raise PlannerError(ErrorCode.not_found, "sprint not found", {"sprint_id": sprint_id})

        candidates: tuple[tuple[str, str, str | None, str | None], ...] = (
            ("title", "title", ticket.title, title),
            ("priority", "priority", ticket.priority.value, priority.value),
            ("deadline", "deadline", ticket.deadline, deadline),
            (
                "implementer",
                "implementer",
                ticket.implementer.value if ticket.implementer is not None else None,
                implementer.value if implementer is not None else None,
            ),
            ("project_id", "project_id", ticket.project_id, project_id),
            ("sprint_id", "sprint_id", ticket.sprint_id, sprint_id),
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
        for field, _column, previous, updated in changes:
            append_event(
                conn,
                ticket_id,
                EventKind.ticket_updated,
                {"field": field, "from": previous, "to": updated},
                now,
            )
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


def write_recap(
    conn: sqlite3.Connection, ticket_id: str, *, body: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket, worker_type_definition = _load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id
        )
        admission.check_recap_writable(
            ticket.stage,
            worker_type_definition=worker_type_definition,
        )
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?", (body, now, ticket_id)
        )
        append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
        ticket_worker_context.set_ticket_changed(conn, ticket_id, actor)
        return _load_ticket_for_write(conn, ticket_id)


def assign_ticket_to_sprint_item(
    conn: sqlite3.Connection, ticket_id: str, *, sprint_item_id: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        if (
            conn.execute("SELECT 1 FROM sprint_items WHERE id = ?", (sprint_item_id,)).fetchone()
            is None
        ):
            raise PlannerError(
                ErrorCode.not_found, "sprint item not found", {"sprint_item_id": sprint_item_id}
            )
        if ticket.sprint_item_id is not None and ticket.sprint_item_id != sprint_item_id:
            raise PlannerError(
                ErrorCode.validation,
                "ticket is already assigned to a sprint item",
                {"ticket_id": ticket_id, "sprint_item_id": ticket.sprint_item_id},
            )
        prev_item = ticket.sprint_item_id
        prev_sprint = ticket.sprint_id
        prev_project = ticket.project_id
        conn.execute(
            "UPDATE tickets SET sprint_item_id = ?, sprint_id = NULL, project_id = NULL, "
            "updated_at = ? WHERE id = ?",
            (sprint_item_id, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {
                "field": "sprint_item_id",
                "from": prev_item,
                "to": sprint_item_id,
                "cleared_sprint_id": prev_sprint,
                "cleared_project": prev_project,
            },
            now,
        )
        if prev_item != sprint_item_id:
            _append_item_children_changed(conn, prev_item, ticket_id, "parentage", now)
            _append_item_children_changed(conn, sprint_item_id, ticket_id, "parentage", now)
        return _load_ticket_for_write(conn, ticket_id)


def remove_ticket_from_sprint_item(
    conn: sqlite3.Connection, ticket_id: str, *, sprint_item_id: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket_for_write(conn, ticket_id)
        item = conn.execute(
            "SELECT sprint_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is None:
            raise PlannerError(
                ErrorCode.not_found, "sprint item not found", {"sprint_item_id": sprint_item_id}
            )
        if ticket.sprint_item_id != sprint_item_id:
            raise PlannerError(
                ErrorCode.validation,
                "ticket is not assigned to this sprint item",
                {
                    "ticket_id": ticket_id,
                    "sprint_item_id": sprint_item_id,
                    "actual_sprint_item_id": ticket.sprint_item_id,
                },
            )
        parent_sprint_id: str | None = item["sprint_id"]
        conn.execute(
            "UPDATE tickets SET sprint_item_id = NULL, sprint_id = ?, updated_at = ? WHERE id = ?",
            (parent_sprint_id, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {
                "field": "sprint_item_id",
                "from": sprint_item_id,
                "to": None,
                "sprint_id": parent_sprint_id,
            },
            now,
        )
        _append_item_children_changed(conn, sprint_item_id, ticket_id, "parentage", now)
        return _load_ticket_for_write(conn, ticket_id)
