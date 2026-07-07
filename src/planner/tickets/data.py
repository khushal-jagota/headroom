"""The only module that writes ticket rows. State, ceiling/at_cap, and fields
value mutations happen in exactly one function (_apply_decision); every public
writer is one BEGIN IMMEDIATE transaction. Plain field writers (note, recap,
priority, deadline, sprint) do their own single-column UPDATE and never touch
state/ceiling/at_cap/fields.value. sqlite3, events and ids live here only; the
clock arrives as now (unix seconds) and the title limit as an argument."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Final

from planner.core.contracts import EventKind, Priority, Project
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
from planner.tickets.logic import admission, fields_codec, resolution
from planner.tickets.logic.decisions import Decision


class _Unset:
    """Typed sentinel for set_run_status: distinguishes 'leave chat_session_key untouched'
    from 'set it to None'."""


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
    project_raw = row["project"]
    return Ticket(
        id=row["id"],
        title=row["title"],
        state=TicketState(row["state"]),
        priority=Priority(row["priority"]),
        deadline=row["deadline"],
        project=Project(project_raw) if project_raw is not None else None,
        sprint_item_id=row["sprint_item_id"],
        sprint_id=row["sprint_id"],
        recap=row["recap"],
        ceiling=TicketState(row["ceiling"]),
        at_cap=AtCap(row["at_cap"]),
        status=TicketStatus(row["status"]),
        worker=row["worker"],
        chat_session_key=row["chat_session_key"],
        alias=row["alias"],
        fields=fields_codec.fields_from_json(row["fields"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
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
    return _load_ticket(conn, ticket.id)


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
    now: int,
    title_max_chars: int,
    project: Project | None = None,
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
            if project is not None:
                raise PlannerError(ErrorCode.validation, "project is derived when parented")
        elif sprint_id is not None:
            exists = conn.execute("SELECT 1 FROM sprints WHERE id = ?", (sprint_id,)).fetchone()
            if exists is None:
                raise PlannerError(
                    ErrorCode.not_found, "sprint not found", {"sprint_id": sprint_id}
                )
        conn.execute(
            "INSERT INTO tickets (id, title, state, priority, deadline, project, sprint_item_id, "
            "sprint_id, recap, ceiling, at_cap, status, worker, "
            "chat_session_key, alias, fields, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)",
            (
                ticket_id,
                title,
                TicketState.needs_success.value,
                priority.value,
                deadline,
                project.value if project is not None else None,
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
        return _load_ticket(conn, ticket_id)


def read_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    return _load_ticket(conn, ticket_id)


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


def set_run_status(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    status: TicketStatus,
    worker: str | None,
    session_key: str | None | _Unset = _UNSET,
    error: str | None = None,
    now: int,
) -> Ticket:
    """The single door for the ticket's code-owned run status (System B is the only caller).
    Writes status + worker together in ONE atomic UPDATE — dissolving the runless orphan (there is
    no second write to crash between) — optionally advancing the mind's durable chat_session_key,
    then appends one ticket_status_changed event. Passing session_key=None clears the key; leaving
    it as the _UNSET sentinel leaves the stored key untouched. `error` is surfaced in the event
    payload only (it never affects the row), so an errored run's reason lands in the debug log."""
    with _txn(conn):
        _load_ticket(conn, ticket_id)  # existence guard -> not_found
        if isinstance(session_key, _Unset):
            conn.execute(
                "UPDATE tickets SET status = ?, worker = ?, updated_at = ? WHERE id = ?",
                (status.value, worker, now, ticket_id),
            )
        else:
            conn.execute(
                "UPDATE tickets SET status = ?, worker = ?, chat_session_key = ?, updated_at = ? "
                "WHERE id = ?",
                (status.value, worker, session_key, now, ticket_id),
            )
        payload: dict[str, object] = {"status": status.value, "worker": worker}
        if error is not None:
            payload["error"] = error
        append_event(conn, ticket_id, EventKind.ticket_status_changed, payload, now)
        return _load_ticket(conn, ticket_id)


def start_run_if_runnable(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    worker: str | None,
    guard: Callable[[sqlite3.Connection, Ticket], bool] | None,
    now: int,
) -> Ticket | None:
    """The atomic guarded start transition (System B's only start-write door). In ONE
    BEGIN IMMEDIATE txn: re-read the ticket, apply `guard` (System A's readiness) against that
    fresh, lock-consistent view, and only if it passes write status=agent_working + worker and
    append one ticket_status_changed event; otherwise write nothing and return None. This closes
    the poll->run TOCTOU — a human drop/grant-stop/block/park committed in the gap is either seen
    by the re-read (guard skips) or blocked until this commits — while keeping System B the sole
    status writer. `guard=None` => an unconditional start (W3a's bare set_off path)."""
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        if guard is not None and not guard(conn, ticket):
            return None
        conn.execute(
            "UPDATE tickets SET status = ?, worker = ?, updated_at = ? WHERE id = ?",
            (TicketStatus.agent_working.value, worker, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_status_changed,
            {"status": TicketStatus.agent_working.value, "worker": worker},
            now,
        )
        return _load_ticket(conn, ticket_id)


def file_proposal(
    conn: sqlite3.Connection, ticket_id: str, *, field: FieldName, body: str, actor: str, now: int
) -> Ticket:
    with _txn(conn):
        ticket = _load_ticket(conn, ticket_id)
        decision = resolution.decide_file_proposal(ticket, field, body, actor, now)
        return _apply_decision(conn, ticket, decision, now)


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
        return _apply_decision(conn, ticket, decision, now)


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


def change_grant(
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
        decision = resolution.decide_grant_change(ticket, ceiling, at_cap, actor)
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
