"""Ticket routes (§9), plus the ticket-anchored links and the ticket-centric
derived views (board, queues). Thin HTTP shells over the stage-3 writers and the
pure read views: every handler is parse -> auth -> writer -> serialize. No route
re-implements a domain rule and no route appends events.

This module also homes the shared request plumbing (config/clock accessors, the
per-request connection dependency, the transaction context manager, and the enum
and body-key marshallers). The other api modules import these from here; the direction
is strictly one-way (sprints/days/dispatch api -> tickets api), and views modules
import no api module at all.

Request bodies arrive as plain dicts and are marshalled into the TypedDict shapes
declared in each domain's contracts (§14); a null or wrong-typed key raises the
validation envelope rather than FastAPI's 422, so the CLI and UI see one error shape."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from planner.core import link_actions
from planner.core.authctx import (
    RequestContext,
    reject_agent_fields,
    request_context,
    require_chief,
    require_direct_write,
)
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict, LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date, resolve_day_id
from planner.projects import data as projects_data
from planner.runtime.contracts import EmployeeRevisionRunner
from planner.runtime.readiness_doorbell import ReadinessDoorbell
from planner.sprints import views as sprints_views
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AcceptBody,
    AtCap,
    CreateTicketBody,
    CreateTicketFromExternalWorkBody,
    FieldName,
    LinkBody,
    NextCeiling,
    NoteBody,
    ProposeBody,
    ProposeWithRecapBody,
    RecapBody,
    ReconcileTicketFromExternalWorkBody,
    RevisionMessageBody,
    ScopeBody,
    StateBody,
    TicketState,
    ValueEditBody,
)

router = APIRouter()

# §8: workers drive priority/deadline/day/sprint via `ticket set`; title, project, and
# user note are direct-only, so an attributed non-Chief PATCH is agent_forbidden.
_TICKET_DIRECT_ONLY_FIELDS = ("title", "project", "project_id", "user_note")


# --- shared plumbing (imported by the other api modules) -----------------------


def get_config(request: Request) -> Config:
    return cast(Config, request.app.state.config)


def get_clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


async def db_conn(request: Request) -> AsyncIterator[sqlite3.Connection]:
    conn = cast(Callable[[], sqlite3.Connection], request.app.state.conn_factory)()
    try:
        yield conn
    finally:
        conn.close()


def get_readiness_doorbell(request: Request) -> ReadinessDoorbell:
    return cast(ReadinessDoorbell, request.app.state.readiness_doorbell)


def get_employee_revision_runner(request: Request) -> EmployeeRevisionRunner | None:
    runner: EmployeeRevisionRunner | None = getattr(
        request.app.state, "employee_step_runner", None
    )
    return runner


DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]
Doorbell = Annotated[ReadinessDoorbell, Depends(get_readiness_doorbell)]
EmployeeRunner = Annotated[
    EmployeeRevisionRunner | None, Depends(get_employee_revision_runner)
]


@contextmanager
def txn(conn: sqlite3.Connection) -> Iterator[None]:
    """Wrap a NON-self-transacting writer (days/dispatch/links) so a mid-sequence
    PlannerError rolls back cleanly. Never wrap a ticket/sprint writer — those open
    their own BEGIN IMMEDIATE and would raise 'transaction within a transaction'."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def parse_enum[E: StrEnum](enum_cls: type[E], raw: str, what: str) -> E:
    try:
        return enum_cls(raw)
    except ValueError:
        raise PlannerError(ErrorCode.validation, f"invalid {what}", {what: raw}) from None


def body_str(body: JsonDict, key: str, default: str = "") -> str:
    """Marshal an optional string body key: absent -> default; null or non-string ->
    the validation envelope. Unknown keys are ignored by the callers, matching the
    §14 body shapes."""
    raw = body.get(key, default)
    if not isinstance(raw, str):
        raise PlannerError(ErrorCode.validation, f"invalid {key}", {key: raw})
    return raw


def body_opt_str(body: JsonDict, key: str) -> str | None:
    raw = body.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PlannerError(ErrorCode.validation, f"invalid {key}", {key: raw})
    return raw


# --- request-body marshallers (contract shapes in tickets/contracts.py) ---------


def _marshal_create_ticket(raw: JsonDict) -> CreateTicketBody:
    return CreateTicketBody(
        title=body_str(raw, "title"),
        user_note=body_str(raw, "user_note"),
        priority=body_opt_str(raw, "priority"),
        deadline=body_opt_str(raw, "deadline"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        sprint_id=body_opt_str(raw, "sprint_id"),
        sprint_item_id=body_opt_str(raw, "sprint_item_id"),
    )


_EXTERNAL_RECONCILE_KEYS = frozenset(
    {"state", "user_note", "recap", "success", "approach", "plan", "result"}
)
_EXTERNAL_CREATE_KEYS = _EXTERNAL_RECONCILE_KEYS | frozenset(
    {
        "title",
        "priority",
        "deadline",
        "project",
        "project_id",
        "sprint_id",
        "sprint_item_id",
    }
)


def _reject_unknown_external_keys(raw: JsonDict, allowed: frozenset[str]) -> None:
    unknown = [key for key in raw if key not in allowed]
    if unknown:
        raise PlannerError(
            ErrorCode.validation,
            "unknown external-work field",
            {"field": unknown[0]},
        )


def _marshal_external_reconcile(raw: JsonDict) -> ReconcileTicketFromExternalWorkBody:
    _reject_unknown_external_keys(raw, _EXTERNAL_RECONCILE_KEYS)
    missing = [key for key in ("state", "user_note") if key not in raw]
    if missing:
        raise PlannerError(
            ErrorCode.validation,
            "external-work reconciliation requires state and user_note",
            {"missing": missing},
        )
    body = ReconcileTicketFromExternalWorkBody(
        state=body_str(raw, "state"),
        user_note=body_str(raw, "user_note"),
    )
    for key in ("recap", "success", "approach", "plan", "result"):
        if key in raw:
            body[key] = body_str(raw, key)
    return body


def _marshal_external_create(raw: JsonDict) -> CreateTicketFromExternalWorkBody:
    _reject_unknown_external_keys(raw, _EXTERNAL_CREATE_KEYS)
    if "title" not in raw:
        raise PlannerError(
            ErrorCode.validation,
            "external-work creation requires title",
            {"missing": ["title"]},
        )
    common_raw = {key: value for key, value in raw.items() if key in _EXTERNAL_RECONCILE_KEYS}
    common = _marshal_external_reconcile(common_raw)
    body = CreateTicketFromExternalWorkBody(
        state=common["state"],
        user_note=common["user_note"],
        title=body_str(raw, "title"),
    )
    for key in ("recap", "success", "approach", "plan", "result"):
        if key in common:
            body[key] = common[key]
    if "priority" in raw:
        body["priority"] = body_opt_str(raw, "priority")
    if "deadline" in raw:
        body["deadline"] = body_opt_str(raw, "deadline")
    if "project" in raw:
        body["project"] = body_opt_str(raw, "project")
    if "project_id" in raw:
        body["project_id"] = body_opt_str(raw, "project_id")
    if "sprint_id" in raw:
        body["sprint_id"] = body_opt_str(raw, "sprint_id")
    if "sprint_item_id" in raw:
        body["sprint_item_id"] = body_opt_str(raw, "sprint_item_id")
    return body


def _external_values(
    body: ReconcileTicketFromExternalWorkBody,
) -> dict[FieldName, str]:
    values: dict[FieldName, str] = {}
    for field in FieldName:
        if field.value in body:
            values[field] = body[field.value]
    return values


def _marshal_accept(raw: JsonDict) -> AcceptBody:
    return AcceptBody(
        edited_body=body_opt_str(raw, "edited_body"),
        next_ceiling=body_opt_str(raw, "next_ceiling"),
        at_cap=body_opt_str(raw, "at_cap"),
    )


# --- scope marshallers ---------------------------------------------------------


def _parse_next_ceiling(raw: str | None) -> NextCeiling | None:
    if raw is None:
        return None
    if raw == NO_FURTHER:
        return NO_FURTHER
    try:
        return TicketState(raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown next_ceiling", {"next_ceiling": raw}
        ) from None


def _parse_scope_at_cap(raw: str | None) -> AtCap | None:
    if raw is None:
        return None
    try:
        return AtCap(raw)
    except ValueError:
        raise PlannerError(ErrorCode.scope_invalid, "unknown at_cap", {"at_cap": raw}) from None


# --- ticket routes -------------------------------------------------------------


@router.post("/tickets")
async def create_ticket(raw: dict[str, Any], conn: DbConn, ctx: Ctx, cfg: Cfg,
                        clk: Clk, readiness_doorbell: Doorbell) -> JsonDict:
    body = _marshal_create_ticket(raw)
    now = clk.now_unix()
    priority = parse_enum(Priority, body["priority"], "priority") \
        if body["priority"] is not None else Priority.P3
    project = projects_data.resolve_project(
        conn,
        project_id=body["project_id"],
        project_name=body["project"],
    )
    ticket = tickets_actions.create_ticket(
        conn,
        title=body["title"],
        actor=ctx.actor,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        user_note=body["user_note"],
        project_id=project.id if project is not None else None,
        priority=priority,
        deadline=body["deadline"],
        sprint_id=body["sprint_id"],
        sprint_item_id=body["sprint_item_id"],
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/chief/tickets/from-external-work")
async def create_ticket_from_external_work(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_chief(ctx)
    body = _marshal_external_create(raw)
    target_state = parse_enum(TicketState, body["state"], "state")
    priority_raw = body.get("priority")
    priority = (
        parse_enum(Priority, priority_raw, "priority")
        if priority_raw is not None
        else Priority.P3
    )
    project = projects_data.resolve_project(
        conn,
        project_id=body.get("project_id"),
        project_name=body.get("project"),
    )
    now = clk.now_unix()
    ticket = tickets_actions.create_ticket_from_external_work(
        conn,
        title=body["title"],
        user_note=body["user_note"],
        target_state=target_state,
        provided_values=_external_values(body),
        recap=body.get("recap"),
        actor=ctx.actor,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        project_id=project.id if project is not None else None,
        priority=priority,
        deadline=body.get("deadline"),
        sprint_id=body.get("sprint_id"),
        sprint_item_id=body.get("sprint_item_id"),
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/chief/tickets/{ticket_id}/reconcile-from-external-work")
async def reconcile_ticket_from_external_work(
    ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_chief(ctx)
    body = _marshal_external_reconcile(raw)
    target_state = parse_enum(TicketState, body["state"], "state")
    now = clk.now_unix()
    ticket = tickets_actions.reconcile_ticket_from_external_work(
        conn,
        ticket_id,
        user_note=body["user_note"],
        target_state=target_state,
        provided_values=_external_values(body),
        recap=body.get("recap"),
        actor=ctx.actor,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.get("/tickets")
async def list_tickets(conn: DbConn, cfg: Cfg, clk: Clk, state: str | None = None,
                       project: str | None = None, project_id: str | None = None,
                       sprint_id: str | None = None,
                       sprint_item_id: str | None = None, day: str | None = None) -> JsonDict:
    state_enum = parse_enum(TicketState, state, "state") if state is not None else None
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    # `day` ('today' | ISO) scopes the list to one day's board via the day_tickets join.
    day_id = resolve_day_id(day, clk.now(), cfg.boundary_hour) if day is not None else None
    return {
        "tickets": tickets_views.list_tickets(
            conn,
            clk.now_unix(),
            state=state_enum,
            project_id=resolved_project.id if resolved_project is not None else None,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
            day_id=day_id,
        )
    }


@router.get("/tickets/by-session/{session_key}")
async def get_my_ticket(session_key: str, conn: DbConn, clk: Clk) -> JsonDict:
    """A worker agent's own ticket, resolved from its Hermes session key."""
    ticket = tickets_data.read_ticket_by_session_key(conn, session_key)
    return tickets_views.ticket_detail(conn, ticket.id, clk.now_unix())


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, conn: DbConn, clk: Clk) -> JsonDict:
    return tickets_views.ticket_detail(conn, ticket_id, clk.now_unix())


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(
    ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_direct_write(ctx)
    deleted = tickets_actions.delete_ticket(
        conn,
        ticket_id,
        actor=ctx.actor,
        now=clk.now_unix(),
        readiness_doorbell=readiness_doorbell,
    )
    return {
        "ok": True,
        "ticket_id": deleted.ticket_id,
        "title": deleted.title,
        "day_ids": list(deleted.day_ids),
        "sprint_item_ids": list(deleted.sprint_item_ids),
        "sprint_ids": list(deleted.sprint_ids),
        "linked_entity_ids": list(deleted.linked_entity_ids),
    }


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(ticket_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx,
                       cfg: Cfg, clk: Clk) -> JsonDict:
    recognized = (
        "title", "user_note", "priority", "deadline", "project", "project_id", "sprint_id",
    )
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no ticket fields to update", {})
    reject_agent_fields(ctx, body, _TICKET_DIRECT_ONLY_FIELDS)
    now = clk.now_unix()
    if "title" in body:
        tickets_data.set_title(
            conn,
            ticket_id,
            title=body_str(body, "title"),
            title_max_chars=TITLE_MAX_CHARS,
            actor=ctx.actor,
            now=now,
        )
    if "user_note" in body:
        tickets_data.set_user_note(
            conn,
            ticket_id,
            user_note=body_str(body, "user_note"),
            actor=ctx.actor,
            now=now,
        )
    if "priority" in body:
        priority = parse_enum(Priority, body_str(body, "priority"), "priority")
        tickets_data.set_priority(conn, ticket_id, priority=priority, actor=ctx.actor, now=now)
    if "deadline" in body:
        deadline = body_opt_str(body, "deadline")
        tickets_data.set_deadline(
            conn, ticket_id, deadline=deadline, actor=ctx.actor, now=now
        )
    if "project" in body or "project_id" in body:
        project_raw = body_opt_str(body, "project")
        project_id_raw = body_opt_str(body, "project_id")
        project = projects_data.resolve_project(
            conn, project_id=project_id_raw, project_name=project_raw
        )
        tickets_data.set_project(
            conn,
            ticket_id,
            project_id=project.id if project is not None else None,
            actor=ctx.actor,
            now=now,
        )
    if "sprint_id" in body:
        sprint_id = body_opt_str(body, "sprint_id")
        tickets_data.set_sprint(
            conn, ticket_id, sprint_id=sprint_id, actor=ctx.actor, now=now
        )
    return tickets_views.ticket_json(tickets_data.read_ticket(conn, ticket_id), now)


@router.post("/tickets/{ticket_id}/propose")
async def propose_current_field(ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                                clk: Clk) -> JsonDict:
    body = ProposeWithRecapBody(
        body=body_str(raw, "body"),
        recap=body_str(raw, "recap"),
    )
    now = clk.now_unix()
    ticket = tickets_data.file_current_proposal_with_recap(
        conn,
        ticket_id,
        body=body["body"],
        recap=body["recap"],
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/propose/{field}")
async def propose_field(ticket_id: str, field: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                        clk: Clk) -> JsonDict:
    body = ProposeBody(body=body_str(raw, "body"))
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    ticket = tickets_data.file_proposal(
        conn, ticket_id, field=field_enum, body=body["body"], actor=ctx.actor, now=now
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/accept/{field}")
async def accept_field(ticket_id: str, field: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                       clk: Clk, readiness_doorbell: Doorbell) -> JsonDict:
    body = _marshal_accept(raw)
    require_direct_write(ctx)
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    next_ceiling = _parse_next_ceiling(body["next_ceiling"])
    at_cap = _parse_scope_at_cap(body["at_cap"])
    ticket = tickets_actions.accept_proposal(
        conn,
        ticket_id,
        field=field_enum,
        actor=ctx.actor,
        now=now,
        edited_body=body["edited_body"],
        next_ceiling=next_ceiling,
        at_cap=at_cap,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_actions.approve_review(
        conn,
        ticket_id,
        actor=ctx.actor,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/return-for-revision")
async def return_ticket_for_revision(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    employee_runner: EmployeeRunner,
) -> JsonDict:
    body = RevisionMessageBody(message=body_str(raw, "message"))
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_actions.return_ticket_for_revision(
        conn,
        ticket_id,
        message=body["message"],
        actor=ctx.actor,
        now=now,
        employee_revision_runner=employee_runner,
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/notes/{field}")
async def put_notes(ticket_id: str, field: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                    clk: Clk) -> JsonDict:
    body = NoteBody(note=body_opt_str(raw, "note"), user_note=body_opt_str(raw, "user_note"))
    if "note" in raw and "user_note" in raw:
        raise PlannerError(ErrorCode.validation, "use note or user_note, not both", {})
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    ticket = tickets_data.set_note(
        conn,
        ticket_id,
        field=field_enum,
        note=body["user_note"] if "user_note" in raw else body["note"],
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/recap")
async def put_recap(ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                    clk: Clk) -> JsonDict:
    body = RecapBody(body=body_str(raw, "body"))
    now = clk.now_unix()
    ticket = tickets_data.write_recap(conn, ticket_id, body=body["body"], actor=ctx.actor, now=now)
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/value/{field}")
async def put_value(ticket_id: str, field: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                    clk: Clk, readiness_doorbell: Doorbell) -> JsonDict:
    body = ValueEditBody(body=body_str(raw, "body"))
    require_direct_write(ctx)
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    ticket = tickets_actions.edit_field_value(
        conn,
        ticket_id,
        field=field_enum,
        new_body=body["body"],
        actor=ctx.actor,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/scope")
async def scope_ticket(ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                       clk: Clk, readiness_doorbell: Doorbell) -> JsonDict:
    body = ScopeBody(ceiling=body_opt_str(raw, "ceiling"), at_cap=body_opt_str(raw, "at_cap"))
    require_direct_write(ctx)
    now = clk.now_unix()
    ceiling_raw = body["ceiling"]
    at_cap_raw = body["at_cap"]
    if ceiling_raw is None or at_cap_raw is None:
        missing = [name for name, value in (("ceiling", ceiling_raw), ("at_cap", at_cap_raw))
                   if value is None]
        raise PlannerError(
            ErrorCode.scope_missing, "scope requires ceiling and at_cap", {"missing": missing}
        )
    try:
        ceiling = TicketState(ceiling_raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown ceiling", {"ceiling": ceiling_raw}
        ) from None
    try:
        at_cap = AtCap(at_cap_raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown at_cap", {"at_cap": at_cap_raw}
        ) from None
    ticket = tickets_actions.change_scope(
        conn,
        ticket_id,
        ceiling=ceiling,
        at_cap=at_cap,
        actor=ctx.actor,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/state")
async def set_state(ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                    clk: Clk, readiness_doorbell: Doorbell) -> JsonDict:
    body = StateBody(to=body_str(raw, "to"))
    require_direct_write(ctx)
    now = clk.now_unix()
    to_state = parse_enum(TicketState, body["to"], "state")
    ticket = tickets_actions.set_state(
        conn,
        ticket_id,
        new_state=to_state,
        actor=ctx.actor,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/drop")
async def drop_ticket(
    ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_actions.drop_ticket(
        conn,
        ticket_id,
        actor=ctx.actor,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/takeover")
async def take_over_ticket(
    ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_actions.take_over_ticket(
        conn,
        ticket_id,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/release")
async def release_ticket(
    ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_actions.release_ticket(
        conn,
        ticket_id,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return tickets_views.ticket_json(ticket, now)


@router.get("/tickets/{ticket_id}/events")
async def ticket_events(ticket_id: str, conn: DbConn, cfg: Cfg) -> JsonDict:
    tickets_data.read_ticket(conn, ticket_id)
    return {"events": tickets_views.list_events_for_entity(conn, ticket_id, cfg.events_read_limit)}


@router.get("/tickets/{ticket_id}/copy-text", response_class=PlainTextResponse)
async def ticket_copy_text(ticket_id: str, conn: DbConn) -> str:
    return tickets_views.copy_text(conn, ticket_id)


@router.post("/links")
async def add_link(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk,
    readiness_doorbell: Doorbell,
) -> JsonDict:
    body = LinkBody(
        from_id=body_str(raw, "from_id"),
        to_id=body_str(raw, "to_id"),
        kind=body_str(raw, "kind"),
    )
    kind = parse_enum(LinkKind, body["kind"], "kind")
    now = clk.now_unix()
    link_actions.add_link(
        conn,
        body["from_id"],
        body["to_id"],
        kind,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return {"from_id": body["from_id"], "to_id": body["to_id"], "kind": kind.value}


@router.delete("/links")
async def remove_link(conn: DbConn, ctx: Ctx, clk: Clk,
                      readiness_doorbell: Doorbell, from_id: str, to_id: str,
                      kind: str) -> JsonDict:
    kind_enum = parse_enum(LinkKind, kind, "kind")
    now = clk.now_unix()
    link_actions.remove_link(
        conn,
        from_id,
        to_id,
        kind_enum,
        now=now,
        readiness_doorbell=readiness_doorbell,
    )
    return {"ok": True}


@router.get("/board")
async def board(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return tickets_views.board_view(conn, clk.now_unix(), day_id=day_id)


@router.get("/queues")
async def queues(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    now = clk.now_unix()
    today_iso = planning_date(clk.now(), cfg.boundary_hour).isoformat()
    item_approval_rows = sprints_views.approval_item_rows(conn)
    item_overdue_rows = sprints_views.overdue_item_rows(conn)
    return tickets_views.queues_view(conn, now, today_iso, item_approval_rows, item_overdue_rows)
