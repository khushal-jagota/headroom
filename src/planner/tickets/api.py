"""Ticket routes (§9), plus the ticket-anchored links and the ticket-centric
derived views (board, queues). Thin HTTP shells over the stage-3 writers and the
pure read views: every handler is parse -> auth -> writer -> serialize. No route
re-implements a domain rule and no route appends events.

This module also homes the shared request plumbing (config/clock accessors, the
per-request connection dependency, the transaction context manager, and the enum
marshaller). The other api modules import these from here; the direction is strictly
one-way (sprints/days/dispatch api -> tickets api), and views modules import no api
module at all."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from planner.core import links as core_links
from planner.core.authctx import (
    RequestContext,
    reject_agents,
    request_context,
    require_claim,
    validate_carried_claim,
)
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import EventKind, JsonDict, LinkKind, Priority, Project
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.days.logic.dates import planning_date
from planner.dispatch import data as dispatch_data
from planner.sprints import views as sprints_views
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    FieldName,
    NextCeiling,
    Ticket,
    TicketState,
)
from planner.tickets.logic import admission

router = APIRouter()


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


DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]


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


# --- request models ------------------------------------------------------------


class CreateTicketBody(BaseModel):
    title: str = ""
    priority: str | None = None
    deadline: str | None = None
    project: str | None = None
    sprint_id: str | None = None
    sprint_item_id: str | None = None


class ProposeBody(BaseModel):
    body: str = ""


class AcceptBody(BaseModel):
    edited_body: str | None = None
    next_ceiling: str | None = None
    at_cap: str | None = None


class NoteBody(BaseModel):
    note: str | None = None


class RecapBody(BaseModel):
    body: str = ""


class GrantBody(BaseModel):
    ceiling: str | None = None
    at_cap: str | None = None


class StateBody(BaseModel):
    to: str = ""


class LinkBody(BaseModel):
    from_id: str = ""
    to_id: str = ""
    kind: str = ""


# --- grant marshallers ---------------------------------------------------------


def _parse_next_ceiling(raw: str | None) -> NextCeiling | None:
    if raw is None:
        return None
    if raw == NO_FURTHER:
        return NO_FURTHER
    try:
        return TicketState(raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.grant_invalid, "unknown next_ceiling", {"next_ceiling": raw}
        ) from None


def _parse_grant_at_cap(raw: str | None) -> AtCap | None:
    if raw is None:
        return None
    try:
        return AtCap(raw)
    except ValueError:
        raise PlannerError(ErrorCode.grant_invalid, "unknown at_cap", {"at_cap": raw}) from None


# --- private gap-fill writers (D2; A1: writer-shaped for relocation) ------------


def _set_title(conn: sqlite3.Connection, ticket_id: str, title: str, *,
               title_max_chars: int, now: int) -> Ticket:
    admission.validate_title(title, title_max_chars)
    with txn(conn):
        ticket = tickets_data.read_ticket(conn, ticket_id)
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
        return tickets_data.read_ticket(conn, ticket_id)


def _set_project(conn: sqlite3.Connection, ticket_id: str, project: Project | None, *,
                 now: int) -> Ticket:
    with txn(conn):
        ticket = tickets_data.read_ticket(conn, ticket_id)
        if ticket.sprint_item_id is not None:
            raise PlannerError(ErrorCode.validation, "project is derived when parented")
        prev = ticket.project.value if ticket.project is not None else None
        new_value = project.value if project is not None else None
        conn.execute(
            "UPDATE tickets SET project = ?, updated_at = ? WHERE id = ?",
            (new_value, now, ticket_id),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_updated,
            {"field": "project", "from": prev, "to": new_value},
            now,
        )
        return tickets_data.read_ticket(conn, ticket_id)


# --- ticket routes -------------------------------------------------------------


@router.post("/tickets")
async def create_ticket(body: CreateTicketBody, conn: DbConn, ctx: Ctx, cfg: Cfg,
                        clk: Clk) -> JsonDict:
    now = clk.now_unix()
    priority = parse_enum(Priority, body.priority, "priority") if body.priority is not None \
        else Priority.P3
    project = parse_enum(Project, body.project, "project") if body.project is not None else None
    ticket = tickets_data.create_ticket(
        conn,
        title=body.title,
        actor=ctx.actor,
        now=now,
        title_max_chars=cfg.title_max_chars,
        project=project,
        priority=priority,
        deadline=body.deadline,
        sprint_id=body.sprint_id,
        sprint_item_id=body.sprint_item_id,
    )
    return tickets_views.ticket_json(ticket, now)


@router.get("/tickets")
async def list_tickets(conn: DbConn, clk: Clk, state: str | None = None,
                       project: str | None = None, sprint_id: str | None = None,
                       sprint_item_id: str | None = None) -> JsonDict:
    state_enum = parse_enum(TicketState, state, "state") if state is not None else None
    project_enum = parse_enum(Project, project, "project") if project is not None else None
    return {
        "tickets": tickets_views.list_tickets(
            conn,
            clk.now_unix(),
            state=state_enum,
            project=project_enum,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
        )
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, conn: DbConn, clk: Clk) -> JsonDict:
    return tickets_views.ticket_detail(conn, ticket_id, clk.now_unix())


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(ticket_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx,
                       cfg: Cfg, clk: Clk) -> JsonDict:
    recognized = ("title", "priority", "deadline", "project", "sprint_id")
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no ticket fields to update", {})
    now = clk.now_unix()
    validate_carried_claim(conn, ctx, ticket_id, now)
    if "title" in body:
        _set_title(conn, ticket_id, body["title"], title_max_chars=cfg.title_max_chars, now=now)
    if "priority" in body:
        priority = parse_enum(Priority, body["priority"], "priority")
        tickets_data.set_priority(conn, ticket_id, priority=priority, actor=ctx.actor, now=now)
    if "deadline" in body:
        tickets_data.set_deadline(conn, ticket_id, deadline=body["deadline"], actor=ctx.actor,
                                  now=now)
    if "project" in body:
        project = parse_enum(Project, body["project"], "project") \
            if body["project"] is not None else None
        _set_project(conn, ticket_id, project, now=now)
    if "sprint_id" in body:
        tickets_data.set_sprint(conn, ticket_id, sprint_id=body["sprint_id"], actor=ctx.actor,
                                now=now)
    return tickets_views.ticket_json(tickets_data.read_ticket(conn, ticket_id), now)


@router.post("/tickets/{ticket_id}/propose/{field}")
async def propose_field(ticket_id: str, field: str, body: ProposeBody, conn: DbConn, ctx: Ctx,
                        clk: Clk) -> JsonDict:
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    if ctx.is_claimed_agent:
        require_claim(conn, ctx, ticket_id, now)
    ticket = tickets_data.file_proposal(
        conn, ticket_id, field=field_enum, body=body.body, actor=ctx.actor, now=now
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/accept/{field}")
async def accept_field(ticket_id: str, field: str, body: AcceptBody, conn: DbConn, ctx: Ctx,
                       clk: Clk) -> JsonDict:
    reject_agents(ctx)
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    next_ceiling = _parse_next_ceiling(body.next_ceiling)
    at_cap = _parse_grant_at_cap(body.at_cap)
    ticket = tickets_data.accept_proposal(
        conn,
        ticket_id,
        field=field_enum,
        actor=ctx.actor,
        now=now,
        edited_body=body.edited_body,
        next_ceiling=next_ceiling,
        at_cap=at_cap,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    now = clk.now_unix()
    ticket = tickets_data.approve_review(conn, ticket_id, actor=ctx.actor, now=now)
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/notes/{field}")
async def put_notes(ticket_id: str, field: str, body: NoteBody, conn: DbConn, ctx: Ctx,
                    clk: Clk) -> JsonDict:
    field_enum = parse_enum(FieldName, field, "field")
    now = clk.now_unix()
    if ctx.is_claimed_agent:
        require_claim(conn, ctx, ticket_id, now)
    ticket = tickets_data.set_note(
        conn, ticket_id, field=field_enum, note=body.note, actor=ctx.actor, now=now
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/recap")
async def put_recap(ticket_id: str, body: RecapBody, conn: DbConn, ctx: Ctx,
                    clk: Clk) -> JsonDict:
    now = clk.now_unix()
    if ctx.is_claimed_agent:
        require_claim(conn, ctx, ticket_id, now)
    ticket = tickets_data.write_recap(conn, ticket_id, body=body.body, actor=ctx.actor, now=now)
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/grant")
async def grant_ticket(ticket_id: str, body: GrantBody, conn: DbConn, ctx: Ctx,
                       clk: Clk) -> JsonDict:
    reject_agents(ctx)
    now = clk.now_unix()
    if body.ceiling is None or body.at_cap is None:
        missing = [name for name, raw in (("ceiling", body.ceiling), ("at_cap", body.at_cap))
                   if raw is None]
        raise PlannerError(
            ErrorCode.grant_missing, "grant requires ceiling and at_cap", {"missing": missing}
        )
    try:
        ceiling = TicketState(body.ceiling)
    except ValueError:
        raise PlannerError(
            ErrorCode.grant_invalid, "unknown ceiling", {"ceiling": body.ceiling}
        ) from None
    try:
        at_cap = AtCap(body.at_cap)
    except ValueError:
        raise PlannerError(
            ErrorCode.grant_invalid, "unknown at_cap", {"at_cap": body.at_cap}
        ) from None
    ticket = tickets_data.change_grant(
        conn, ticket_id, ceiling=ceiling, at_cap=at_cap, actor=ctx.actor, now=now
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/state")
async def set_state(ticket_id: str, body: StateBody, conn: DbConn, ctx: Ctx,
                    clk: Clk) -> JsonDict:
    reject_agents(ctx)
    now = clk.now_unix()
    to_state = parse_enum(TicketState, body.to, "state")
    ticket = tickets_data.set_state(conn, ticket_id, new_state=to_state, actor=ctx.actor, now=now)
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/drop")
async def drop_ticket(ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    now = clk.now_unix()
    ticket = tickets_data.drop_ticket(conn, ticket_id, actor=ctx.actor, now=now)
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/unblock")
async def unblock_ticket(ticket_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    now = clk.now_unix()
    with txn(conn):
        dispatch_data.clear_auto_block(conn, ticket_id, now)
    return tickets_views.ticket_json(tickets_data.read_ticket(conn, ticket_id), now)


@router.get("/tickets/{ticket_id}/events")
async def ticket_events(ticket_id: str, conn: DbConn, cfg: Cfg) -> JsonDict:
    tickets_data.read_ticket(conn, ticket_id)
    return {"events": tickets_views.list_events_for_entity(conn, ticket_id, cfg.events_read_limit)}


@router.get("/tickets/{ticket_id}/runs")
async def ticket_runs(ticket_id: str, conn: DbConn) -> JsonDict:
    tickets_data.read_ticket(conn, ticket_id)
    return {"runs": tickets_views.runs_for_ticket(conn, ticket_id)}


@router.get("/tickets/{ticket_id}/copy-text", response_class=PlainTextResponse)
async def ticket_copy_text(ticket_id: str, conn: DbConn) -> str:
    return tickets_views.copy_text(conn, ticket_id)


def _validate_link_claim(conn: sqlite3.Connection, ctx: RequestContext,
                         from_id: str, to_id: str, now: int) -> None:
    """§7.6 for link writes: a claim-carrying request must hold the active claim on a
    ticket endpoint of the link (either side — an agent may link its own ticket in
    both directions). Non-ticket endpoints have no claim to validate against; a claim
    matching neither ticket endpoint is rejected naming the from-side mismatch."""
    if not ctx.is_claimed_agent:
        return None
    ticket_ids = [
        entity_id
        for entity_id in (from_id, to_id)
        if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (entity_id,)).fetchone()
        is not None
    ]
    first_error: PlannerError | None = None
    for ticket_id in ticket_ids:
        try:
            require_claim(conn, ctx, ticket_id, now)
            return None
        except PlannerError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
    return None


@router.post("/links")
async def add_link(body: LinkBody, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    kind = parse_enum(LinkKind, body.kind, "kind")
    now = clk.now_unix()
    _validate_link_claim(conn, ctx, body.from_id, body.to_id, now)
    with txn(conn):
        core_links.add_link(conn, body.from_id, body.to_id, kind, now)
    return {"from_id": body.from_id, "to_id": body.to_id, "kind": kind.value}


@router.delete("/links")
async def remove_link(conn: DbConn, ctx: Ctx, clk: Clk, from_id: str, to_id: str,
                      kind: str) -> JsonDict:
    kind_enum = parse_enum(LinkKind, kind, "kind")
    now = clk.now_unix()
    _validate_link_claim(conn, ctx, from_id, to_id, now)
    with txn(conn):
        core_links.remove_link(conn, from_id, to_id, kind_enum, now)
    return {"ok": True}


@router.get("/board")
async def board(conn: DbConn, clk: Clk) -> JsonDict:
    return tickets_views.board_view(conn, clk.now_unix())


@router.get("/queues")
async def queues(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    now = clk.now_unix()
    today_iso = planning_date(clk.now(), cfg.boundary_hour).isoformat()
    item_approval_rows = sprints_views.approval_item_rows(conn)
    item_overdue_rows = sprints_views.overdue_item_rows(conn)
    return tickets_views.queues_view(conn, now, today_iso, item_approval_rows, item_overdue_rows)
