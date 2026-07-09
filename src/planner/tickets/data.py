"""The only module that writes ticket rows. State, ceiling/at_cap, and fields
value mutations happen in exactly one function (_apply_decision); every public
writer is one BEGIN IMMEDIATE transaction. Plain field writers (title, project,
note, recap, priority, deadline, sprint) do their own single-column UPDATE and never touch
state/ceiling/at_cap/fields.value. sqlite3, events and ids live here only; the
clock arrives as now (unix seconds) and the title limit as an argument."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Final

from planner.chat import data as chat_data
from planner.core.contracts import EventKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import ID_PREFIXES, new_id
from planner.tickets.contracts import (
    AtCap,
    FieldName,
    FieldSlot,
    NextCeiling,
    Ticket,
    TicketFields,
    TicketState,
    TicketStatus,
)
from planner.tickets.logic import admission, fields_codec, machine, resolution
from planner.tickets.logic.decisions import Decision


class _Unset:
    """Typed sentinel for status helpers: leave chat_session_key untouched."""


_UNSET: Final = _Unset()


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
    return Ticket(
        id=row["id"],
        title=row["title"],
        state=TicketState(row["state"]),
        priority=Priority(row["priority"]),
        deadline=row["deadline"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        sprint_item_id=row["sprint_item_id"],
        sprint_id=row["sprint_id"],
        recap=row["recap"],
        ceiling=TicketState(row["ceiling"]),
        at_cap=AtCap(row["at_cap"]),
        ticket_status=TicketStatus(row["ticket_status"]),
        chat_session_key=row["chat_session_key"],
        alias=row["alias"],
        fields=fields_codec.fields_from_json(row["fields"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    row = conn.execute(
        "SELECT tickets.*, projects.name AS project_name "
        "FROM tickets LEFT JOIN projects ON projects.id = tickets.project_id "
        "WHERE tickets.id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
    return _row_to_ticket(row)


def _apply_decision(
    conn: sqlite3.Connection, ticket: Ticket, decision: Decision, now: int
) -> Ticket:
    new_fields = decision.new_fields if decision.new_fields is not None else ticket.fields
    new_state = decision.new_state if decision.new_state is not None else ticket.state
    new_ceiling = decision.new_ceiling if decision.new_ceiling is not None else ticket.ceiling
    new_at_cap = decision.new_at_cap if decision.new_at_cap is not None else ticket.at_cap
    conn.execute(
        "UPDATE tickets SET fields = ?, state = ?, ceiling = ?, at_cap = ?, updated_at = ? "
        "WHERE id = ?",
        (
            fields_codec.fields_to_json(new_fields),
            new_state.value,
            new_ceiling.value,
            new_at_cap.value,
            now,
            ticket.id,
        ),
    )
    for spec in decision.events:
        append_event(conn, ticket.id, spec.kind, spec.payload, now)
    if any(spec.kind is EventKind.state_changed for spec in decision.events):
        _append_item_children_changed(conn, ticket.sprint_item_id, ticket.id, "state", now)
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
    row = conn.execute(
        "SELECT sprint_item_id FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
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
        ticket = _load_ticket(conn, ticket_id)
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
        return _load_ticket(conn, ticket_id)


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
    now: int,
    title_max_chars: int,
    project_id: str | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
) -> Ticket:
    admission.validate_title(title, title_max_chars)
    admission.validate_deadline(deadline)
    ticket_id = new_id(ID_PREFIXES["ticket"])
    empty_fields = fields_codec.fields_to_json(TicketFields())
    with _txn(conn):
        if sprint_item_id is not None:
            if conn.execute(
                "SELECT 1 FROM sprint_items WHERE id = ?", (sprint_item_id,)
            ).fetchone() is None:
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
            "id, title, state, priority, deadline, project_id, sprint_item_id, "
            "sprint_id, recap, ceiling, at_cap, ticket_status, "
            "chat_session_key, alias, fields, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, NULL, NULL, ?, ?, ?)",
            (
                ticket_id,
                title,
                TicketState.needs_success.value,
                priority.value,
                deadline,
                project_id,
                sprint_item_id,
                sprint_id,
                TicketState.needs_success.value,
                AtCap.propose.value,
                TicketStatus.empty.value,
                empty_fields,
                now,
                now,
            ),
        )
        append_event(conn, ticket_id, EventKind.ticket_created, {}, now)
        _append_item_children_changed(conn, sprint_item_id, ticket_id, "created", now)
        return _load_ticket(conn, ticket_id)


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


def start_run_if_runnable(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    guard: Callable[[sqlite3.Connection, Ticket], bool] | None,
    now: int,
) -> Ticket | None:
    """Start a run only from empty after the injected readiness guard passes."""
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        if ticket.ticket_status is not TicketStatus.empty:
            return None
        if guard is not None and not guard(conn, ticket):
            return None
        _write_ticket_status(conn, ticket_id, TicketStatus.agent_running_step, now)
        return _load_ticket(conn, ticket_id)


def finish_run_if_still_running_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    session_key: str | None | _Unset = _UNSET,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
        if ticket.ticket_status is TicketStatus.agent_running_step:
            _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return _load_ticket(conn, ticket_id)


def mark_run_errored(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    error: str,
    session_key: str | None | _Unset = _UNSET,
    now: int,
) -> Ticket:
    with _txn(conn):
        _load_ticket(conn, ticket_id)
        _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
        _write_ticket_status(conn, ticket_id, TicketStatus.errored, now, error=error)
        return _load_ticket(conn, ticket_id)


def mark_run_errored_if_still_running_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    error: str,
    session_key: str | None | _Unset = _UNSET,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        if ticket.ticket_status is TicketStatus.agent_running_step:
            _persist_ticket_chat_session_key(conn, ticket_id, session_key, now)
            _write_ticket_status(conn, ticket_id, TicketStatus.errored, now, error=error)
        return _load_ticket(conn, ticket_id)


def file_proposal(
    conn: sqlite3.Connection, ticket_id: str, *, field: FieldName, body: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_file_proposal(ticket, field, body, actor, now)
        updated = _apply_decision(conn, ticket, decision, now)
        if any(spec.kind is EventKind.proposal_filed for spec in decision.events):
            _write_ticket_status(conn, ticket_id, TicketStatus.awaiting_approval, now)
            updated = _load_ticket(conn, ticket_id)
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
        ticket = _load_ticket(conn, ticket_id)
        field = machine.gating_field(ticket.state)
        if field is None:
            raise PlannerError(
                ErrorCode.validation,
                "ticket state has no proposal field",
                {"state": ticket.state.value},
            )
        decision = resolution.decide_file_proposal(ticket, field, body, actor, now)
        _apply_decision(conn, ticket, decision, now)
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?",
            (recap, now, ticket_id),
        )
        append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
        if any(spec.kind is EventKind.proposal_filed for spec in decision.events):
            _write_ticket_status(conn, ticket_id, TicketStatus.awaiting_approval, now)
        return _load_ticket(conn, ticket_id)


def accept_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: FieldName,
    actor: str,
    now: int,
    edited_body: str | None = None,
    next_ceiling: NextCeiling | None = None,
    at_cap: AtCap | None = None,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_accept(ticket, field, actor, edited_body, next_ceiling, at_cap)
        _apply_decision(conn, ticket, decision, now)
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return _load_ticket(conn, ticket_id)


def take_over_ticket(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    with _txn(conn):
        _load_ticket(conn, ticket_id)
        _write_ticket_status(conn, ticket_id, TicketStatus.user_takeover, now)
        return _load_ticket(conn, ticket_id)


def release_ticket(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> Ticket:
    with _txn(conn):
        _load_ticket(conn, ticket_id)
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return _load_ticket(conn, ticket_id)


def edit_field_value(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: FieldName,
    new_body: str,
    actor: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_edit_value(ticket, field, new_body, actor)
        return _apply_decision(conn, ticket, decision, now)


def approve_review(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_approve(ticket, actor)
        return _apply_decision(conn, ticket, decision, now)


def return_for_revision(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    actor: str,
    now: int,
) -> Ticket:
    admission.validate_body(message, "revision guidance")
    framed_message = (
        "The user rejected your proposal and provided the following guidance:\n\n"
        f"{message.strip()}"
    )
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_return_for_revision(ticket, actor)
        _apply_decision(conn, ticket, decision, now)
        chat_data.record_message(conn, ticket_id, role="human", text=framed_message, now=now)
        _write_ticket_status(conn, ticket_id, TicketStatus.empty, now)
        return _load_ticket(conn, ticket_id)


def set_state(
    conn: sqlite3.Connection, ticket_id: str, *, new_state: TicketState, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_state_jump(ticket, new_state, actor)
        return _apply_decision(conn, ticket, decision, now)


def drop_ticket(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_drop(ticket, actor)
        return _apply_decision(conn, ticket, decision, now)


def change_scope(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    ceiling: TicketState,
    at_cap: AtCap,
    actor: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_scope_change(ticket, ceiling, at_cap, actor)
        return _apply_decision(conn, ticket, decision, now)


def set_note(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: FieldName,
    note: str | None,
    actor: str,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        slot = fields_codec.get_slot(ticket.fields, field)
        new_slot = FieldSlot(value=slot.value, proposal=slot.proposal, notes=note)
        new_fields = fields_codec.with_slot(ticket.fields, field, new_slot)
        conn.execute(
            "UPDATE tickets SET fields = ?, updated_at = ? WHERE id = ?",
            (fields_codec.fields_to_json(new_fields), now, ticket_id),
        )
        append_event(conn, ticket_id, EventKind.note_updated, {"field": field.value}, now)
        return _load_ticket(conn, ticket_id)


def write_recap(
    conn: sqlite3.Connection, ticket_id: str, *, body: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        admission.check_recap_writable(ticket.state)
        conn.execute(
            "UPDATE tickets SET recap = ?, updated_at = ? WHERE id = ?", (body, now, ticket_id)
        )
        append_event(conn, ticket_id, EventKind.recap_updated, {}, now)
        return _load_ticket(conn, ticket_id)


def set_title(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str,
    title_max_chars: int,
    now: int,
) -> Ticket:
    admission.validate_title(title, title_max_chars)
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        prev = ticket.title
        conn.execute(
            "UPDATE tickets SET title = ?, updated_at = ? WHERE id = ?", (title, now, ticket_id)
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {"field": "title", "from": prev, "to": title},
            now,
        )
        return _load_ticket(conn, ticket_id)


def set_project(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    project_id: str | None,
    now: int,
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        if ticket.sprint_item_id is not None:
            raise PlannerError(ErrorCode.validation, "project is derived when parented")
        if project_id is not None and conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (project_id,)
        ).fetchone() is None:
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
        prev = ticket.project_id
        conn.execute(
            "UPDATE tickets SET project_id = ?, updated_at = ? WHERE id = ?",
            (project_id, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {"field": "project_id", "from": prev, "to": project_id},
            now,
        )
        return _load_ticket(conn, ticket_id)


def set_priority(
    conn: sqlite3.Connection, ticket_id: str, *, priority: Priority, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        prev = ticket.priority.value
        conn.execute(
            "UPDATE tickets SET priority = ?, updated_at = ? WHERE id = ?",
            (priority.value, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {"field": "priority", "from": prev, "to": priority.value},
            now,
        )
        return _load_ticket(conn, ticket_id)


def set_deadline(
    conn: sqlite3.Connection, ticket_id: str, *, deadline: str | None, actor: str, now: int
) -> Ticket:
    admission.validate_deadline(deadline)
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        prev = ticket.deadline
        conn.execute(
            "UPDATE tickets SET deadline = ?, updated_at = ? WHERE id = ?",
            (deadline, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {"field": "deadline", "from": prev, "to": deadline},
            now,
        )
        return _load_ticket(conn, ticket_id)


def set_sprint(
    conn: sqlite3.Connection, ticket_id: str, *, sprint_id: str | None, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        admission.check_sprint_assignable(ticket_id, ticket.sprint_item_id)
        if sprint_id is not None and conn.execute(
            "SELECT 1 FROM sprints WHERE id = ?", (sprint_id,)
        ).fetchone() is None:
            raise PlannerError(ErrorCode.not_found, "sprint not found", {"sprint_id": sprint_id})
        prev = ticket.sprint_id
        conn.execute(
            "UPDATE tickets SET sprint_id = ?, updated_at = ? WHERE id = ?",
            (sprint_id, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {"field": "sprint_id", "from": prev, "to": sprint_id},
            now,
        )
        return _load_ticket(conn, ticket_id)


def assign_ticket_to_sprint_item(
    conn: sqlite3.Connection, ticket_id: str, *, sprint_item_id: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        if conn.execute(
            "SELECT 1 FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone() is None:
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
        return _load_ticket(conn, ticket_id)


def remove_ticket_from_sprint_item(
    conn: sqlite3.Connection, ticket_id: str, *, sprint_item_id: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
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
        return _load_ticket(conn, ticket_id)
