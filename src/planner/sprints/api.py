"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Thin HTTP shells
over the stage-3 sprint writers and the sprint read views. Ideas are homed here
because the Idea shape lives in this domain's contracts. Sprint writers take a
`clock: Clock`, not `now: int` — do not mix them up."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter

from planner.core.authctx import reject_agent_fields, require_direct_write
from planner.core.contracts import JsonDict, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date
from planner.projects import data as projects_data
from planner.sprints import data as sprints_data
from planner.sprints import views as sprints_views
from planner.sprints.contracts import (
    KICKOFF_FIELDS,
    MID_SPRINT_FIELDS,
    REVIEW_FIELDS,
    AddItemTicketBody,
    CreateIdeaBody,
    CreateItemBody,
    CreateSprintBody,
    ItemStatus,
)
from planner.tickets import data as tickets_data
from planner.tickets.api import Cfg, Clk, Ctx, DbConn, body_opt_str, body_str, parse_enum

router = APIRouter()

_SPRINT_TEXT_FIELDS = ("name",) + KICKOFF_FIELDS + MID_SPRINT_FIELDS + REVIEW_FIELDS
_ITEM_PLAIN_FIELDS = ("title", "body", "priority", "deadline", "project_id")


# --- request-body marshallers (contract shapes in sprints/contracts.py) ---------


def _marshal_create_item(raw: JsonDict) -> CreateItemBody:
    return CreateItemBody(
        title=body_str(raw, "title"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        body=body_str(raw, "body"),
        priority=body_opt_str(raw, "priority"),
        deadline=body_opt_str(raw, "deadline"),
        sprint_id=body_opt_str(raw, "sprint_id"),
    )


def _marshal_create_sprint(raw: JsonDict) -> CreateSprintBody:
    return CreateSprintBody(
        name=body_str(raw, "name"),
        date_start=body_str(raw, "date_start"),
        date_end=body_str(raw, "date_end"),
        limiting_factor=body_str(raw, "limiting_factor"),
        primary_bet=body_str(raw, "primary_bet"),
        supports=body_str(raw, "supports"),
        premortem=body_str(raw, "premortem"),
    )


def _marshal_create_idea(raw: JsonDict) -> CreateIdeaBody:
    return CreateIdeaBody(
        title=body_str(raw, "title"),
        body=body_str(raw, "body"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
    )


def _marshal_item_deadline(raw: object) -> None:
    if raw is None:
        return
    if not isinstance(raw, str):
        raise PlannerError(ErrorCode.validation, "invalid deadline", {"deadline": raw})
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation, "invalid deadline", {"deadline": raw}
        ) from exc


# --- item routes ---------------------------------------------------------------


@router.post("/items")
async def create_item(raw: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    body = _marshal_create_item(raw)
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"], required=True
    )
    assert project is not None
    priority = parse_enum(Priority, body["priority"], "priority") \
        if body["priority"] is not None else Priority.P3
    _marshal_item_deadline(body["deadline"])
    item = sprints_data.create_item(
        conn,
        title=body["title"],
        project_id=project.id,
        body=body["body"],
        priority=priority,
        deadline=body["deadline"],
        sprint_id=body["sprint_id"],
        clock=clk,
    )
    return sprints_views.item_detail(conn, item.id)


@router.get("/items")
async def list_items(conn: DbConn, status: str | None = None, project: str | None = None,
                     project_id: str | None = None, sprint_id: str | None = None) -> JsonDict:
    status_enum = parse_enum(ItemStatus, status, "status") if status is not None else None
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    return {
        "items": sprints_views.list_items(
            conn,
            status=status_enum,
            project_id=resolved_project.id if resolved_project is not None else None,
            sprint_id_filter=sprint_id,
        )
    }


@router.get("/items/{item_id}")
async def get_item(item_id: str, conn: DbConn) -> JsonDict:
    return sprints_views.item_detail(conn, item_id)


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    require_direct_write(ctx)
    deleted = sprints_data.delete_item(
        conn,
        item_id,
        actor=ctx.actor,
        clock=clk,
    )
    return {
        "ok": True,
        "sprint_item_id": deleted.sprint_item_id,
        "title": deleted.title,
        "sprint_ids": list(deleted.sprint_ids),
        "linked_entity_ids": list(deleted.linked_entity_ids),
    }


@router.post("/items/{item_id}/tickets")
async def add_item_ticket(item_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                          clk: Clk) -> JsonDict:
    require_direct_write(ctx)
    body = AddItemTicketBody(ticket_id=body_str(raw, "ticket_id"))
    tickets_data.assign_ticket_to_sprint_item(
        conn, body["ticket_id"], sprint_item_id=item_id, actor=ctx.actor, now=clk.now_unix()
    )
    return sprints_views.item_detail(conn, item_id)


@router.delete("/items/{item_id}/tickets/{ticket_id}")
async def remove_item_ticket(item_id: str, ticket_id: str, conn: DbConn, ctx: Ctx,
                             clk: Clk) -> JsonDict:
    require_direct_write(ctx)
    tickets_data.remove_ticket_from_sprint_item(
        conn, ticket_id, sprint_item_id=item_id, actor=ctx.actor, now=clk.now_unix()
    )
    return sprints_views.item_detail(conn, item_id)


@router.patch("/items/{item_id}")
async def patch_item(item_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx,
                     clk: Clk) -> JsonDict:
    recognized = set(_ITEM_PLAIN_FIELDS) | {"project", "sprint_id"}
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown item field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no item fields to update", {})
    # Sprint item status is derived from child tickets and blocking links; item patching is
    # only for plain direct-editable fields and sprint placement.
    reject_agent_fields(ctx, body, set(_ITEM_PLAIN_FIELDS) | {"project", "sprint_id"})
    for field in _ITEM_PLAIN_FIELDS:
        if field in body:
            if field == "project_id":
                continue
            if field == "deadline":
                value = body_opt_str(body, "deadline")
                _marshal_item_deadline(value)
            elif field in ("title", "body", "priority"):
                value = body_str(body, field)
            else:
                value = body[field]
            sprints_data.update_item_field(conn, item_id, field, value, clock=clk)
    if "project" in body or "project_id" in body:
        project = projects_data.resolve_project(
            conn,
            project_id=body_opt_str(body, "project_id"),
            project_name=body_opt_str(body, "project"),
            required=True,
        )
        assert project is not None
        sprints_data.update_item_field(conn, item_id, "project_id", project.id, clock=clk)
    if "sprint_id" in body:
        sprints_data.assign_item_sprint(conn, item_id, body_opt_str(body, "sprint_id"), clock=clk)
    return sprints_views.item_detail(conn, item_id)


# --- sprint routes -------------------------------------------------------------


@router.post("/sprints")
async def create_sprint(raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    body = _marshal_create_sprint(raw)
    require_direct_write(ctx)
    for label, value in (("date_start", body["date_start"]), ("date_end", body["date_end"])):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise PlannerError(ErrorCode.validation, f"invalid {label}", {label: value}) from None
    sprint = sprints_data.create_sprint(
        conn,
        name=body["name"],
        date_start=body["date_start"],
        date_end=body["date_end"],
        limiting_factor=body["limiting_factor"],
        primary_bet=body["primary_bet"],
        supports=body["supports"],
        premortem=body["premortem"],
        clock=clk,
    )
    return sprints_views.sprint_json(sprint)


@router.get("/sprints")
async def list_sprints(conn: DbConn) -> JsonDict:
    return {"sprints": sprints_views.list_sprints(conn)}


@router.get("/sprints/{sprint_id}")
async def get_sprint(sprint_id: str, conn: DbConn) -> JsonDict:
    return sprints_views.sprint_json(sprints_data.read_sprint(conn, sprint_id))


@router.patch("/sprints/{sprint_id}")
async def patch_sprint(sprint_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx,
                       clk: Clk) -> JsonDict:
    require_direct_write(ctx)  # §8: agents get `sprint show` only — no sprint edit surface.
    recognized = set(_SPRINT_TEXT_FIELDS) | {"date_start", "date_end"}
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown sprint field", {"field": key})
    # Marshal each field to a string, mirroring the Day PATCH: a null or non-string
    # value raises the validation envelope instead of hitting the NOT NULL constraint
    # or a raw SQLite binding error. A null is treated as absent (skipped).
    edits: dict[str, str] = {}
    for field in _SPRINT_TEXT_FIELDS:
        value = body_opt_str(body, field)
        if value is not None:
            edits[field] = value
    setting_dates = "date_start" in body or "date_end" in body
    if not edits and not setting_dates:
        raise PlannerError(ErrorCode.validation, "no sprint fields to update", {})
    for field, value in edits.items():
        sprints_data.update_sprint_field(conn, sprint_id, field, value, clock=clk)
    if setting_dates:
        sprints_data.set_sprint_dates(
            conn, sprint_id,
            date_start=body_opt_str(body, "date_start"),
            date_end=body_opt_str(body, "date_end"),
            clock=clk,
        )
    return sprints_views.sprint_json(sprints_data.read_sprint(conn, sprint_id))


@router.get("/sprint/current")
async def current_sprint(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    today_iso = planning_date(clk.now(), cfg.boundary_hour).isoformat()
    return sprints_views.sprint_current_view(conn, today_iso, clk.now_unix())


# --- idea routes ---------------------------------------------------------------


@router.post("/ideas")
async def create_idea(raw: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    body = _marshal_create_idea(raw)
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"]
    )
    idea = sprints_data.create_idea(
        conn,
        title=body["title"],
        body=body["body"],
        project_id=project.id if project is not None else None,
        now=clk.now_unix(),
    )
    return sprints_views.idea_json(idea)


@router.get("/ideas")
async def list_ideas(
    conn: DbConn, project: str | None = None, project_id: str | None = None
) -> JsonDict:
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    return {
        "ideas": sprints_views.list_ideas(
            conn,
            project_id=resolved_project.id if resolved_project is not None else None,
        )
    }
