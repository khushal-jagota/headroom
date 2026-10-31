"""Actor-neutral HTTP management for generic scheduled Ticket creation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from planner.conversation.backend_state import require_enabled_model
from planner.conversation.contracts import require_conversation_backend_key
from planner.core import authority
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.managers.assignment import normalize_assignment
from planner.projects import data as projects_data
from planner.scheduled_tickets import actions, data, views
from planner.scheduled_tickets.contracts import (
    ScheduleCadence,
    ScheduledTicketTemplate,
)
from planner.tickets import data as tickets_data
from planner.tickets.api import (
    Clk,
    Ctx,
    DbConn,
    body_opt_str,
    body_str,
    body_str_list,
    parse_enum,
)
from planner.tickets.contracts import TicketLocale
from planner.worker_types.configuration import configured_worker_type_registry

router = APIRouter()

_CREATE_KEYS = frozenset(
    {
        "locale",
        "enabled",
        "cadence",
        "local_time",
        "title",
        "worker_type",
        "kickoff_note",
        "deadline",
        "project",
        "project_id",
        "manager",
        "employee_backend",
        "employee_launch_model",
        "blocked_by_ticket_ids",
    }
)
_PATCH_KEYS = _CREATE_KEYS - {"project"}


def _reject_unknown(raw: JsonDict, allowed: frozenset[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PlannerError(
            ErrorCode.validation,
            "unknown scheduled Ticket schedule fields",
            {"fields": unknown},
        )


def _body_bool(raw: JsonDict, key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise PlannerError(ErrorCode.validation, f"invalid {key}", {key: value})
    return value


def _require_worker_type(worker_type: str) -> str:
    known_worker_types = configured_worker_type_registry().supported_worker_types()
    if worker_type not in known_worker_types:
        raise PlannerError(
            ErrorCode.validation,
            "schedule requires a known worker type",
            {"worker_type": worker_type, "worker_types": list(known_worker_types)},
        )
    return worker_type


def _template_from_create(raw: JsonDict, conn: DbConn) -> ScheduledTicketTemplate:
    worker_type = _require_worker_type(body_str(raw, "worker_type"))
    project = projects_data.resolve_project(
        conn,
        project_id=body_opt_str(raw, "project_id"),
        project_name=body_opt_str(raw, "project"),
    )
    manager_id = None
    manager = raw.get("manager")
    if manager is not None:
        if not isinstance(manager, dict) or set(manager) != {"kind", "id"}:
            raise PlannerError(ErrorCode.validation, "invalid manager")
        kind = manager["kind"]
        if kind == "chief" and manager["id"] == "chief":
            manager_id = None
        elif kind == "manager" and isinstance(manager["id"], str):
            manager_id = manager["id"]
        else:
            raise PlannerError(ErrorCode.validation, "invalid manager")
    else:
        kind = "manager" if manager_id is not None else "chief"
    return ScheduledTicketTemplate(
        locale=(
            parse_enum(TicketLocale, body_str(raw, "locale"), "locale") if "locale" in raw else None
        ),
        manager_kind=kind,
        title=body_str(raw, "title"),
        worker_type=worker_type,
        kickoff_note=body_str(raw, "kickoff_note"),
        deadline=body_opt_str(raw, "deadline"),
        project_id=None if project is None else project.id,
        manager_id=manager_id,
        employee_backend=body_opt_str(raw, "employee_backend"),
        employee_launch_model=body_opt_str(raw, "employee_launch_model"),
        blocked_by_ticket_ids=tuple(body_str_list(raw, "blocked_by_ticket_ids")),
    )


@router.post("/schedules")
async def create_schedule(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    authority.require_above(conn, ctx.principal, authority.owner_only("scheduled_ticket_schedules"))
    raw = normalize_assignment(raw)
    _reject_unknown(raw, _CREATE_KEYS)
    template = _template_from_create(raw, conn)
    launch = tickets_data.requested_ticket_launch_configuration(
        template.worker_type,
        employee_backend=template.employee_backend,
        employee_launch_model=template.employee_launch_model,
    )
    require_enabled_model(conn, require_conversation_backend_key(launch.employee_backend),
                          launch.employee_launch_model or "")
    schedule = actions.create_schedule(
        conn,
        principal=ctx.principal,
        enabled=_body_bool(raw, "enabled", True),
        cadence=parse_enum(
            ScheduleCadence,
            body_str(raw, "cadence", ScheduleCadence.daily.value),
            "cadence",
        ),
        local_time=body_str(raw, "local_time"),
        template=template,
        now=clk.now_unix(),
    )
    return views.schedule_json(schedule, [])


@router.get("/schedules")
async def list_schedules(conn: DbConn) -> JsonDict:
    return {"schedules": [views.schedule_json(item) for item in data.list_schedules(conn)]}


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, conn: DbConn) -> JsonDict:
    return views.schedule_json(
        data.read_schedule(conn, schedule_id),
        data.list_occurrences(conn, schedule_id),
    )


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(
    schedule_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    authority.require_above(conn, ctx.principal, authority.owner_only("scheduled_ticket_schedules"))
    raw = normalize_assignment(raw)
    _reject_unknown(raw, _PATCH_KEYS)
    if not raw:
        raise PlannerError(ErrorCode.validation, "no schedule fields to update", {})
    actions.require_schedule_mutation(data.read_schedule(conn, schedule_id), raw)
    changes: dict[str, object] = {}
    if "locale" in raw:
        changes["locale"] = parse_enum(TicketLocale, body_str(raw, "locale"), "locale")
    if "enabled" in raw:
        changes["enabled"] = _body_bool(raw, "enabled", True)
    if "cadence" in raw:
        changes["cadence"] = parse_enum(ScheduleCadence, body_str(raw, "cadence"), "cadence")
    for key in ("local_time", "title", "worker_type", "kickoff_note"):
        if key in raw:
            changes[key] = body_str(raw, key)
    if "worker_type" in changes:
        changes["worker_type"] = _require_worker_type(str(changes["worker_type"]))
    for key in (
        "deadline",
        "project_id",
        "employee_backend",
        "employee_launch_model",
    ):
        if key in raw:
            changes[key] = body_opt_str(raw, key)
    if "blocked_by_ticket_ids" in raw:
        changes["blocked_by_ticket_ids"] = tuple(body_str_list(raw, "blocked_by_ticket_ids"))
    if "manager" in raw:
        # Reuse the same parser without allowing a second assignment vocabulary.
        assignment = _template_from_create(
            {**views.schedule_json(data.read_schedule(conn, schedule_id)), **raw}, conn
        )
        changes["manager_kind"] = assignment.manager_kind
        changes["manager_id"] = assignment.manager_id
    if {"worker_type", "employee_backend", "employee_launch_model"} & changes.keys():
        existing = data.read_schedule(conn, schedule_id).template
        launch = tickets_data.requested_ticket_launch_configuration(
            body_str(raw, "worker_type", existing.worker_type),
            employee_backend=(
                body_opt_str(raw, "employee_backend")
                if "employee_backend" in raw
                else existing.employee_backend
            ),
            employee_launch_model=(
                body_opt_str(raw, "employee_launch_model")
                if "employee_launch_model" in raw
                else existing.employee_launch_model
            ),
        )
        require_enabled_model(conn, require_conversation_backend_key(launch.employee_backend),
                              launch.employee_launch_model or "")
    schedule = actions.update_schedule(
        conn, schedule_id, changes, principal=ctx.principal, now=clk.now_unix()
    )
    return views.schedule_json(schedule, data.list_occurrences(conn, schedule_id))
