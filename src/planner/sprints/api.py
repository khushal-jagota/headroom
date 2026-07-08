"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Thin HTTP shells
over the stage-3 sprint writers and the sprint read views. Ideas are homed here
because the Idea shape lives in this domain's contracts. Sprint writers take a
`clock: Clock`, not `now: int` — do not mix them up."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter

from planner.core.authctx import reject_agent_fields, reject_agents
from planner.core.contracts import JsonDict, Priority, Project
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date
from planner.sprints import data as sprints_data
from planner.sprints import views as sprints_views
from planner.sprints.contracts import (
    KICKOFF_FIELDS,
    MID_SPRINT_FIELDS,
    REVIEW_FIELDS,
    CreateIdeaBody,
    CreateItemBody,
    CreateSprintBody,
    ItemStatus,
    ProposeStatusBody,
)
from planner.tickets.api import Cfg, Clk, Ctx, DbConn, body_opt_str, body_str, parse_enum

router = APIRouter()

_SPRINT_TEXT_FIELDS = ("name",) + KICKOFF_FIELDS + MID_SPRINT_FIELDS + REVIEW_FIELDS
_ITEM_PLAIN_FIELDS = ("title", "body", "priority", "deadline", "project")


# --- request-body marshallers (contract shapes in sprints/contracts.py) ---------


def _marshal_create_item(raw: JsonDict) -> CreateItemBody:
    return CreateItemBody(
        title=body_str(raw, "title"),
        project=body_opt_str(raw, "project"),
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
    if body["project"] is None:
        raise PlannerError(ErrorCode.validation, "project is required")
    project = parse_enum(Project, body["project"], "project")
    priority = parse_enum(Priority, body["priority"], "priority") \
        if body["priority"] is not None else Priority.P3
    _marshal_item_deadline(body["deadline"])
    item = sprints_data.create_item(
        conn,
        title=body["title"],
        project=project,
        body=body["body"],
        priority=priority,
        deadline=body["deadline"],
        sprint_id=body["sprint_id"],
        clock=clk,
    )
    return {**sprints_views.item_json(item), "blockers_cleared": False}


@router.get("/items")
async def list_items(conn: DbConn, status: str | None = None, project: str | None = None,
                     sprint_id: str | None = None) -> JsonDict:
    status_enum = parse_enum(ItemStatus, status, "status") if status is not None else None
    project_enum = parse_enum(Project, project, "project") if project is not None else None
    return {
        "items": sprints_views.list_items(
            conn, status=status_enum, project=project_enum, sprint_id_filter=sprint_id
        )
    }


@router.get("/items/{item_id}")
async def get_item(item_id: str, conn: DbConn) -> JsonDict:
    return sprints_views.item_detail(conn, item_id)


@router.patch("/items/{item_id}")
async def patch_item(item_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx,
                     clk: Clk) -> JsonDict:
    recognized = set(_ITEM_PLAIN_FIELDS) | {"sprint_id", "status", "blocked_by"}
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown item field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no item fields to update", {})
    if "blocked_by" in body and "status" not in body:
        raise PlannerError(ErrorCode.validation, "blocked_by requires status", {})
    # §3.2/§8: an agent's only item write surface is the status transition (todo↔active,
    # blocked); plain fields and sprint moves are human-only.
    reject_agent_fields(ctx, body, set(_ITEM_PLAIN_FIELDS) | {"sprint_id"})
    for field in _ITEM_PLAIN_FIELDS:
        if field in body:
            if field == "deadline":
                _marshal_item_deadline(body["deadline"])
            sprints_data.update_item_field(conn, item_id, field, body[field], clock=clk)
    if "sprint_id" in body:
        sprints_data.assign_item_sprint(conn, item_id, body["sprint_id"], clock=clk)
    if "status" in body:
        to_status = parse_enum(ItemStatus, body["status"], "status")
        sprints_data.transition_item_status(
            conn, item_id, to_status, clock=clk, by_agent=not ctx.is_human,
            blocked_by=body.get("blocked_by"),
        )
    return sprints_views.item_detail(conn, item_id)


@router.post("/items/{item_id}/propose-status")
async def propose_item_status(item_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                              clk: Clk) -> JsonDict:
    body = ProposeStatusBody(to=body_str(raw, "to"), note=body_opt_str(raw, "note"))
    to_status = parse_enum(ItemStatus, body["to"], "status")
    sprints_data.propose_item_status(
        conn, item_id, to_status, note=body["note"], proposed_by=ctx.actor, clock=clk
    )
    return sprints_views.item_detail(conn, item_id)


@router.post("/items/{item_id}/accept-status")
async def accept_item_status(item_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    sprints_data.accept_item_status(conn, item_id, clock=clk, resolved_by="human")
    return sprints_views.item_detail(conn, item_id)


# --- sprint routes -------------------------------------------------------------


@router.post("/sprints")
async def create_sprint(raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    body = _marshal_create_sprint(raw)
    reject_agents(ctx)
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
    reject_agents(ctx)  # §8: agents get `sprint show` only — no sprint edit surface.
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
    project = parse_enum(Project, body["project"], "project") \
        if body["project"] is not None else None
    idea = sprints_data.create_idea(
        conn, title=body["title"], body=body["body"], project=project, now=clk.now_unix()
    )
    return sprints_views.idea_json(idea)


@router.get("/ideas")
async def list_ideas(conn: DbConn) -> JsonDict:
    return {"ideas": sprints_views.list_ideas(conn)}
