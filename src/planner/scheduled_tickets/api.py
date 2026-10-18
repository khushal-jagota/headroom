"""Actor-neutral HTTP management for generic scheduled Ticket creation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from planner.core.contracts import JsonDict, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.projects import data as projects_data
from planner.scheduled_tickets import actions, data, views
from planner.scheduled_tickets.contracts import (
    ScheduleCadence,
    ScheduledTicketPlacementMode,
    ScheduledTicketTemplate,
)
from planner.tickets.api import (
    Clk,
    DbConn,
    ModelResolver,
    body_opt_str,
    body_str,
    body_str_list,
    parse_enum,
    warm_ticket_creation_model,
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
        "priority",
        "deadline",
        "project",
        "project_id",
        "orchestrator",
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
    priority_raw = body_opt_str(raw, "priority")
    sprint_item_id = None
    orchestrator = raw.get("orchestrator")
    if orchestrator is not None:
        if not isinstance(orchestrator, dict) or set(orchestrator) != {"kind", "id"}:
            raise PlannerError(ErrorCode.validation, "invalid orchestrator")
        kind = orchestrator["kind"]
        if kind == "chief" and orchestrator["id"] == "chief":
            sprint_item_id = None
        elif kind == "sprint_item" and isinstance(orchestrator["id"], str):
            sprint_item_id = orchestrator["id"]
        else:
            raise PlannerError(ErrorCode.validation, "invalid orchestrator")
    else:
        kind = "sprint_item" if sprint_item_id is not None else "chief"
    placement_mode_raw = None
    placement_mode = (
        parse_enum(
            ScheduledTicketPlacementMode,
            placement_mode_raw,
            "placement_mode",
        )
        if placement_mode_raw is not None
        else ScheduledTicketPlacementMode.backlog
    )
    return ScheduledTicketTemplate(
        locale=(
            parse_enum(TicketLocale, body_str(raw, "locale"), "locale") if "locale" in raw else None
        ),
        orchestrator_kind=kind,
        title=body_str(raw, "title"),
        worker_type=worker_type,
        kickoff_note=body_str(raw, "kickoff_note"),
        priority=(
            Priority.P3 if priority_raw is None else parse_enum(Priority, priority_raw, "priority")
        ),
        deadline=body_opt_str(raw, "deadline"),
        project_id=None if project is None else project.id,
        sprint_id=None,
        placement_mode=placement_mode,
        sprint_item_id=sprint_item_id,
        employee_backend=body_opt_str(raw, "employee_backend"),
        employee_launch_model=body_opt_str(raw, "employee_launch_model"),
        blocked_by_ticket_ids=tuple(body_str_list(raw, "blocked_by_ticket_ids")),
    )


@router.post("/schedules")
async def create_schedule(
    raw: dict[str, Any], conn: DbConn, clk: Clk, model_resolver: ModelResolver
) -> JsonDict:
    _reject_unknown(raw, _CREATE_KEYS)
    template = _template_from_create(raw, conn)
    await warm_ticket_creation_model(
        conn, model_resolver, worker_type=template.worker_type,
        employee_backend=template.employee_backend,
        employee_launch_model=template.employee_launch_model,
    )
    schedule = actions.create_schedule(
        conn,
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
    schedule_id: str, raw: dict[str, Any], conn: DbConn, clk: Clk, model_resolver: ModelResolver
) -> JsonDict:
    _reject_unknown(raw, _PATCH_KEYS)
    if not raw:
        raise PlannerError(ErrorCode.validation, "no schedule fields to update", {})
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
    if "priority" in raw:
        changes["priority"] = parse_enum(Priority, body_str(raw, "priority"), "priority")
    for key in (
        "deadline",
        "project_id",
        "sprint_id",
        "sprint_item_id",
        "employee_backend",
        "employee_launch_model",
    ):
        if key in raw:
            changes[key] = body_opt_str(raw, key)
    if "placement_mode" in raw:
        changes["placement_mode"] = parse_enum(
            ScheduledTicketPlacementMode,
            body_str(raw, "placement_mode"),
            "placement_mode",
        )
    if "blocked_by_ticket_ids" in raw:
        changes["blocked_by_ticket_ids"] = tuple(body_str_list(raw, "blocked_by_ticket_ids"))
    if "orchestrator" in raw:
        # Reuse the same parser without allowing a second assignment vocabulary.
        assignment = _template_from_create(
            {**views.schedule_json(data.read_schedule(conn, schedule_id)), **raw}, conn
        )
        changes["orchestrator_kind"] = assignment.orchestrator_kind
        changes["sprint_item_id"] = assignment.sprint_item_id
    if {"worker_type", "employee_backend", "employee_launch_model"} & changes.keys():
        existing = data.read_schedule(conn, schedule_id).template
        await warm_ticket_creation_model(
            conn, model_resolver,
            worker_type=body_str(raw, "worker_type", existing.worker_type),
            employee_backend=(body_opt_str(raw, "employee_backend")
                              if "employee_backend" in raw else existing.employee_backend),
            employee_launch_model=(body_opt_str(raw, "employee_launch_model")
                                   if "employee_launch_model" in raw
                                   else existing.employee_launch_model),
            model_source_is_historical=not (
                {"employee_backend", "employee_launch_model"} & changes.keys()
            ),
        )
    schedule = actions.update_schedule(conn, schedule_id, changes, now=clk.now_unix())
    return views.schedule_json(schedule, data.list_occurrences(conn, schedule_id))
