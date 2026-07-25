"""Ticket routes (§9), plus the ticket-anchored links and the ticket-centric
derived views (board, Review). Thin HTTP shells over the stage-3 writers and the
pure read views: every handler is parse -> auth -> writer -> serialize. No route
re-implements a domain rule.

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

from planner.conversation.employee_configuration import (
    EmployeeConfigurationCatalog,
    EmployeeConfigurationCatalogService,
)
from planner.conversation2.contracts import ConversationSystem
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
from planner.days.logic.dates import resolve_day_id
from planner.projects import data as projects_data
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
    EmployeeConfigurationBody,
    EmployeeLaunchConfiguration,
    LinkBody,
    NoteBody,
    ProposeBody,
    ProposeWithRecapBody,
    RecapBody,
    ReconcileTicketFromExternalWorkBody,
    RevisionMessageBody,
    ScopeBody,
    StageBody,
    StageOwnershipMode,
    Ticket,
    TicketEdit,
    ValueEditBody,
)
from planner.tickets.conversation_projection import TicketConversationProjection
from planner.worker_context.contracts import WorkerContextService
from planner.worker_types.configuration import (
    configured_employee_runtime_definitions,
    configured_worker_type_registry,
)
from planner.worker_types.contracts import WorkerTypeDefinition

router = APIRouter()

# §8: workers drive priority/deadline/day/sprint via `ticket set`; title and project are
# direct-only, so an attributed non-Chief PATCH is agent_forbidden.
_TICKET_DIRECT_ONLY_FIELDS = (
    "title",
    "project",
    "project_id",
)


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


def get_conversation_system(request: Request) -> ConversationSystem:
    return cast(ConversationSystem, request.app.state.conversation_system)


def get_worker_context_service(request: Request) -> WorkerContextService:
    return cast(WorkerContextService, request.app.state.worker_context_service)


DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]
Conversations = Annotated[ConversationSystem, Depends(get_conversation_system)]
WorkerContext = Annotated[WorkerContextService, Depends(get_worker_context_service)]


async def reject_while_the_conversation_is_running(
    conn: sqlite3.Connection,
    conversation_system: ConversationSystem,
    ticket_id: str,
) -> None:
    """Refuse to write over a Ticket whose worker is mid-turn.

    Whether a conversation is live is the conversation system's fact, and asking it is
    awaited, so the question belongs in the route rather than inside a writer. Checking
    here and writing after is racy by nature; for one person driving one workspace that
    is the honest cost of keeping the writers pure database transactions.
    """
    conversation_id = tickets_data.read_ticket(conn, ticket_id).employee_session_id
    if conversation_id is None:
        return
    if await conversation_system.is_running(conversation_id):
        raise PlannerError(
            ErrorCode.already_running,
            "ticket conversation is still running",
            {"ticket_id": ticket_id},
        )


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


def body_str_list(body: JsonDict, key: str) -> list[str]:
    raw = body.get(key, [])
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise PlannerError(ErrorCode.validation, f"invalid {key}", {key: raw})
    return list(raw)


# --- Worker-type-driven ingress helpers -----------------------------------------
# The Ticket's Worker type is resolved first, then each ingress position is validated
# against that Worker type's definition (not a global enum), and a bare str is passed to
# the engine (already str-native and definition-parameterized). This is what lets
# a non-coding Worker type (e.g. probe stages needs_alpha/needs_beta) flow through the
# real routes. Reserved bookends (needs_kickoff/done/dropped) are shared by every
# Worker type (PLAN invariant 1); the Worker-type-specific middle Stages/fields are not.


def _require_create_worker_type(raw: JsonDict) -> str:
    """A create's Worker type, required and known before any writer runs."""
    worker_type = body_str(raw, "worker_type")
    known = configured_worker_type_registry().registered_worker_types()
    if worker_type not in known:
        raise PlannerError(
            ErrorCode.validation,
            "ticket create requires a known worker type",
            {"worker_type": worker_type, "worker_types": list(known)},
        )
    return worker_type


def _ticket_and_worker_type_definition(
    conn: sqlite3.Connection, ticket_id: str
) -> tuple[Ticket, WorkerTypeDefinition]:
    """Load the Ticket and resolve its Worker type for workflow interpretation."""
    ticket = tickets_data.read_ticket(conn, ticket_id)
    return ticket, configured_worker_type_registry().require(ticket.worker_type)


def _validate_field(worker_type_definition: WorkerTypeDefinition, field: str) -> str:
    """A field id validated against the type's declared fields; raises validation."""
    if not worker_type_definition.has_field(field):
        raise PlannerError(
            ErrorCode.validation,
            "unknown ticket field",
            {"field": field, "worker_type": worker_type_definition.worker_type},
        )
    return field


# --- request-body marshallers (contract shapes in tickets/contracts.py) ---------


def _marshal_create_ticket(raw: JsonDict) -> CreateTicketBody:
    body = CreateTicketBody(
        worker_type=_require_create_worker_type(raw),
        title=body_str(raw, "title"),
        kickoff_note=body_str(raw, "kickoff_note"),
        priority=body_opt_str(raw, "priority"),
        deadline=body_opt_str(raw, "deadline"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        sprint_id=body_opt_str(raw, "sprint_id"),
        sprint_item_id=body_opt_str(raw, "sprint_item_id"),
        blocked_by_ticket_ids=body_str_list(raw, "blocked_by_ticket_ids"),
    )
    if "employee_backend" in raw:
        body["employee_backend"] = body_str(raw, "employee_backend")
    return body


# The fixed external-work keys, allowed for every type. The field-value keys are
# per-type (the type's declared field ids minus kickoff, which arrives as
# kickoff_note), so the allowed set is computed once the type is resolved.
_EXTERNAL_FIXED_RECONCILE_KEYS = frozenset({"stage", "kickoff_note", "recap"})
_EXTERNAL_FIXED_CREATE_KEYS = _EXTERNAL_FIXED_RECONCILE_KEYS | frozenset(
    {
        "title",
        "worker_type",
        "employee_backend",
        "priority",
        "deadline",
        "project",
        "project_id",
        "sprint_id",
        "sprint_item_id",
        "blocked_by_ticket_ids",
    }
)


def _external_field_keys(
    worker_type_definition: WorkerTypeDefinition,
) -> tuple[str, ...]:
    """The type's field-value body keys: its declared field ids minus kickoff (which
    arrives as kickoff_note)."""
    return tuple(field for field in worker_type_definition.field_ids() if field != "kickoff")


def _reject_unknown_external_keys(raw: JsonDict, allowed: frozenset[str]) -> None:
    unknown = [key for key in raw if key not in allowed]
    if unknown:
        raise PlannerError(
            ErrorCode.validation,
            "unknown external-work field",
            {"field": unknown[0]},
        )


def _marshal_external_reconcile(
    raw: JsonDict, worker_type_definition: WorkerTypeDefinition
) -> ReconcileTicketFromExternalWorkBody:
    field_keys = _external_field_keys(worker_type_definition)
    _reject_unknown_external_keys(raw, _EXTERNAL_FIXED_RECONCILE_KEYS | frozenset(field_keys))
    missing = [key for key in ("stage", "kickoff_note") if key not in raw]
    if missing:
        raise PlannerError(
            ErrorCode.validation,
            "external-work reconciliation requires stage and kickoff_note",
            {"missing": missing},
        )
    body = ReconcileTicketFromExternalWorkBody(
        stage=body_str(raw, "stage"),
        kickoff_note=body_str(raw, "kickoff_note"),
    )
    if "recap" in raw:
        body["recap"] = body_str(raw, "recap")
    return body


def _marshal_external_create(
    raw: JsonDict, worker_type_definition: WorkerTypeDefinition
) -> CreateTicketFromExternalWorkBody:
    field_keys = _external_field_keys(worker_type_definition)
    _reject_unknown_external_keys(raw, _EXTERNAL_FIXED_CREATE_KEYS | frozenset(field_keys))
    if "title" not in raw:
        raise PlannerError(
            ErrorCode.validation,
            "external-work creation requires title",
            {"missing": ["title"]},
        )
    common_keys = _EXTERNAL_FIXED_RECONCILE_KEYS | frozenset(field_keys)
    common_raw = {key: value for key, value in raw.items() if key in common_keys}
    common = _marshal_external_reconcile(common_raw, worker_type_definition)
    body = CreateTicketFromExternalWorkBody(
        stage=common["stage"],
        kickoff_note=common["kickoff_note"],
        title=body_str(raw, "title"),
        worker_type=body_str(raw, "worker_type"),
    )
    if "recap" in common:
        body["recap"] = common["recap"]
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
    if "employee_backend" in raw:
        body["employee_backend"] = body_str(raw, "employee_backend")
    body["blocked_by_ticket_ids"] = body_str_list(raw, "blocked_by_ticket_ids")
    return body


def _external_values(
    raw: JsonDict,
    kickoff_note: str,
    worker_type_definition: WorkerTypeDefinition,
) -> dict[str, str]:
    """The provided settled values keyed by field id: kickoff from kickoff_note, then
    each declared non-kickoff field present in the raw body (validated as a str). The
    field keys are type-declared, so this is genuinely per-type."""
    values: dict[str, str] = {"kickoff": kickoff_note}
    for key in _external_field_keys(worker_type_definition):
        if key in raw:
            values[key] = body_str(raw, key)
    return values


def _validate_external_stage(stage: str, worker_type_definition: WorkerTypeDefinition) -> str:
    """An external-work target Stage validated against the Worker type's Stages.

    A Stage that is neither linear nor the reserved ``dropped`` is rejected at
    the ingress; a linear-but-out-of-range target (needs_kickoff / dropped) is left for
    ``decide_external_work`` to reject with its exact message (coding parity)."""
    if worker_type_definition.is_known_stage(stage):
        return stage
    raise PlannerError(ErrorCode.validation, "invalid stage", {"stage": stage})


def _marshal_accept(raw: JsonDict) -> AcceptBody:
    return AcceptBody(
        edited_body=body_opt_str(raw, "edited_body"),
        next_ceiling=body_opt_str(raw, "next_ceiling"),
        at_cap=body_opt_str(raw, "at_cap"),
    )


# --- scope marshallers ---------------------------------------------------------


def _parse_next_ceiling(
    raw: str | None, worker_type_definition: WorkerTypeDefinition
) -> str | None:
    if raw is None:
        return None
    if raw == NO_FURTHER:
        return NO_FURTHER
    if raw not in worker_type_definition.ceiling_range():
        raise PlannerError(ErrorCode.scope_invalid, "unknown next_ceiling", {"next_ceiling": raw})
    return raw


def _parse_scope_at_cap(raw: str | None) -> AtCap | None:
    if raw is None:
        return None
    try:
        return AtCap(raw)
    except ValueError:
        raise PlannerError(ErrorCode.scope_invalid, "unknown at_cap", {"at_cap": raw}) from None


# --- ticket routes -------------------------------------------------------------


@router.post("/tickets")
async def create_ticket(
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> JsonDict:
    body = _marshal_create_ticket(raw)
    now = clk.now_unix()
    priority = (
        parse_enum(Priority, body["priority"], "priority")
        if body["priority"] is not None
        else Priority.P3
    )
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
        kickoff_note=body["kickoff_note"],
        project_id=project.id if project is not None else None,
        priority=priority,
        deadline=body["deadline"],
        sprint_id=body["sprint_id"],
        sprint_item_id=body["sprint_item_id"],
        worker_type=body["worker_type"],
        employee_backend=body.get("employee_backend"),
        blocked_by_ticket_ids=body["blocked_by_ticket_ids"],
        planning_now=clk.now(),
        boundary_hour=cfg.boundary_hour,
        sprint_id_explicit="sprint_id" in raw,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/chief/tickets/from-external-work")
async def create_ticket_from_external_work(
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> JsonDict:
    require_chief(ctx)
    worker_type = _require_create_worker_type(raw)
    worker_type_definition = configured_worker_type_registry().require(worker_type)
    body = _marshal_external_create(raw, worker_type_definition)
    target_stage = _validate_external_stage(body["stage"], worker_type_definition)
    priority_raw = body.get("priority")
    priority = (
        parse_enum(Priority, priority_raw, "priority") if priority_raw is not None else Priority.P3
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
        kickoff_note=body["kickoff_note"],
        target_stage=target_stage,
        provided_values=_external_values(raw, body["kickoff_note"], worker_type_definition),
        recap=body.get("recap"),
        actor=ctx.actor,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        project_id=project.id if project is not None else None,
        priority=priority,
        deadline=body.get("deadline"),
        sprint_id=body.get("sprint_id"),
        sprint_item_id=body.get("sprint_item_id"),
        worker_type=worker_type,
        employee_backend=body.get("employee_backend"),
        blocked_by_ticket_ids=body.get("blocked_by_ticket_ids", []),
        planning_now=clk.now(),
        boundary_hour=cfg.boundary_hour,
        sprint_id_explicit="sprint_id" in raw,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/chief/tickets/{ticket_id}/reconcile-from-external-work")
async def reconcile_ticket_from_external_work(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    require_chief(ctx)
    await reject_while_the_conversation_is_running(conn, conversations, ticket_id)
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    body = _marshal_external_reconcile(raw, worker_type_definition)
    target_stage = _validate_external_stage(body["stage"], worker_type_definition)
    now = clk.now_unix()
    ticket = tickets_data.reconcile_ticket_from_external_work(
        conn,
        ticket_id,
        kickoff_note=body["kickoff_note"],
        target_stage=target_stage,
        provided_values=_external_values(raw, body["kickoff_note"], worker_type_definition),
        recap=body.get("recap"),
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.get("/tickets")
async def list_tickets(
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    stage: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
    day: str | None = None,
) -> JsonDict:
    # Stage is stored canonical data. Listing compares it directly and does not need
    # to resolve or interpret the Ticket's Worker type.
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    # `day` ('today' | ISO) scopes the list to one day's board via the day_tickets join.
    day_id = resolve_day_id(day, clk.now(), cfg.boundary_hour) if day is not None else None
    return {
        "tickets": tickets_views.list_tickets(
            conn,
            clk.now_unix(),
            stage=stage,
            project_id=resolved_project.id if resolved_project is not None else None,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
            day_id=day_id,
        )
    }


def _employee_configuration_catalog_service(
    request: Request,
) -> EmployeeConfigurationCatalogService:
    conversation = getattr(request.app.state, "conversation", None)
    service = (
        getattr(conversation, "employee_configuration_catalog", None)
        if conversation is not None
        else None
    )
    if service is None:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "employee configuration catalog is unavailable",
            {},
        )
    return cast(EmployeeConfigurationCatalogService, service)


async def _load_employee_configuration_catalog(
    request: Request,
    employee_backend: str,
    candidate_model: str | None,
    force_refresh: bool = False,
) -> EmployeeConfigurationCatalog:
    service = _employee_configuration_catalog_service(request)
    try:
        if force_refresh:
            return await service.catalog(
                employee_backend, candidate_model, force_refresh=True
            )
        return await service.catalog(employee_backend, candidate_model)
    except PlannerError:
        raise
    except Exception as error:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "employee configuration catalog is unavailable",
            {},
        ) from error


@router.get("/employee-configuration-catalog")
async def get_employee_configuration_catalog(
    request: Request,
    employee_backend: str,
    candidate_model: str | None = None,
    force_refresh: bool = False,
) -> JsonDict:
    definitions = configured_employee_runtime_definitions()
    registered_backend = definitions.employee_backend_catalog.require_registered(employee_backend)
    catalog = await _load_employee_configuration_catalog(
        request, registered_backend, candidate_model, force_refresh
    )
    return catalog.model_dump(mode="json")


@router.get("/tickets/{ticket_id}/worker-self")
async def get_worker_self_ticket(
    ticket_id: str,
    conn: DbConn,
    clk: Clk,
) -> JsonDict:
    """A worker agent's own Ticket, resolved from `PLAN_TICKET_ID` (its spawn env, flag-on).

    Unlike the plain `/tickets/{ticket_id}` detail read, this worker-identity route applies
    the same one-owner validation the by-session readers apply: if the ticket has a bound
    durable session, that session must resolve to EXACTLY this ticket — a corrupt duplicate
    (two tickets sharing one durable session) is rejected. Returns the same detail shape
    (`ticket_detail` + the worker specialist skill) the by-session route returns."""
    ticket = tickets_data.read_ticket(conn, ticket_id)
    if ticket.employee_session_id is not None:
        owner = tickets_data.read_ticket_by_employee_session_id(conn, ticket.employee_session_id)
        if owner.id != ticket.id:
            raise PlannerError(
                ErrorCode.validation,
                "ticket durable session is owned by another ticket",
                {
                    "ticket_id": ticket.id,
                    "employee_session_id": ticket.employee_session_id,
                    "owner_ticket_id": owner.id,
                },
            )
    detail = tickets_views.ticket_detail(conn, ticket.id, clk.now_unix())
    detail["worker"] = (
        configured_worker_type_registry()
        .require(ticket.worker_type)
        .worker_profile.specialist_skill
    )
    return detail


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, conn: DbConn, clk: Clk) -> JsonDict:
    return tickets_views.ticket_detail(conn, ticket_id, clk.now_unix())


@router.post("/tickets/{ticket_id}/acknowledge-completed-response")
async def acknowledge_ticket_completed_response(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> JsonDict:
    """Record that a direct user has opened the Ticket's completed response."""
    require_direct_write(ctx)
    tickets_data.read_ticket(conn, ticket_id)
    changed = TicketConversationProjection(
        cfg.db_path,
        now=clk.now_unix,
        busy_timeout_ms=cfg.db_busy_timeout_ms,
    ).acknowledge_completed_response(ticket_id)
    return {"acknowledged": changed}


@router.put("/tickets/{ticket_id}/employee-configuration")
async def put_ticket_employee_configuration(
    ticket_id: str,
    raw: dict[str, Any],
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    required_keys = {
        "employee_backend",
        "employee_launch_model",
        "employee_launch_reasoning_effort",
    }
    if set(raw) != required_keys:
        raise PlannerError(
            ErrorCode.validation,
            "Employee configuration update requires the exact complete body",
            {},
        )
    body = EmployeeConfigurationBody(
        employee_backend=body_str(raw, "employee_backend"),
        employee_launch_model=body_opt_str(raw, "employee_launch_model"),
        employee_launch_reasoning_effort=body_opt_str(
            raw, "employee_launch_reasoning_effort"
        ),
    )
    definitions = configured_employee_runtime_definitions()
    expected = tickets_data.employee_launch_configuration(
        tickets_data.read_ticket(conn, ticket_id)
    )
    registered_backend = definitions.employee_backend_catalog.require_registered(
        body["employee_backend"]
    )
    advertised_models: frozenset[str] | None = None
    reasoning_supported: bool | None = None
    advertised_reasoning_efforts: frozenset[str] | None = None
    candidate = EmployeeLaunchConfiguration(
        employee_backend=registered_backend,
        employee_launch_model=body["employee_launch_model"],
        employee_launch_reasoning_effort=body["employee_launch_reasoning_effort"],
    )
    if registered_backend == expected.employee_backend and candidate != expected:
        catalog = await _load_employee_configuration_catalog(
            request,
            registered_backend,
            body["employee_launch_model"],
        )
        advertised_models = frozenset(option.value for option in catalog.models)
        reasoning_supported = catalog.reasoning_supported
        advertised_reasoning_efforts = frozenset(
            option.value for option in catalog.reasoning_efforts
        )
    ticket = tickets_data.write_employee_configuration(
        conn,
        ticket_id,
        expected_employee_configuration=expected,
        employee_backend=body["employee_backend"],
        employee_launch_model=body["employee_launch_model"],
        employee_launch_reasoning_effort=body["employee_launch_reasoning_effort"],
        employee_backend_catalog=definitions.employee_backend_catalog,
        advertised_models=advertised_models,
        reasoning_supported=reasoning_supported,
        advertised_reasoning_efforts=advertised_reasoning_efforts,
        now=clk.now_unix(),
    )
    return tickets_views.ticket_detail(conn, ticket.id, clk.now_unix())


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    require_direct_write(ctx)
    await reject_while_the_conversation_is_running(conn, conversations, ticket_id)
    deleted = tickets_data.delete_ticket(
        conn,
        ticket_id,
        actor=ctx.actor,
        now=clk.now_unix(),
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
async def patch_ticket(
    ticket_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, cfg: Cfg, clk: Clk
) -> JsonDict:
    recognized = (
        "title",
        "priority",
        "deadline",
        "project",
        "project_id",
        "sprint_id",
    )
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no ticket fields to update", {})
    reject_agent_fields(ctx, body, _TICKET_DIRECT_ONLY_FIELDS)

    edit = TicketEdit()
    if "title" in body:
        edit["title"] = body_str(body, "title")
    if "priority" in body:
        edit["priority"] = parse_enum(Priority, body_str(body, "priority"), "priority")
    if "deadline" in body:
        edit["deadline"] = body_opt_str(body, "deadline")

    if "project" in body or "project_id" in body:
        project_raw = body_opt_str(body, "project")
        project_id_raw = body_opt_str(body, "project_id")
        project = projects_data.resolve_project(
            conn, project_id=project_id_raw, project_name=project_raw
        )
        edit["project_id"] = project.id if project is not None else None
    if "sprint_id" in body:
        edit["sprint_id"] = body_opt_str(body, "sprint_id")

    now = clk.now_unix()
    ticket = tickets_data.edit_ticket(
        conn,
        ticket_id,
        edit=edit,
        title_max_chars=TITLE_MAX_CHARS,
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/propose")
async def propose_current_field(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
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
async def propose_field(
    ticket_id: str,
    field: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    body = ProposeBody(body=body_str(raw, "body"))
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    now = clk.now_unix()
    ticket = tickets_data.file_proposal(
        conn,
        ticket_id,
        field=field,
        body=body["body"],
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/accept/{field}")
async def accept_field(
    ticket_id: str,
    field: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    body = _marshal_accept(raw)
    require_direct_write(ctx)
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    now = clk.now_unix()
    next_ceiling = _parse_next_ceiling(body["next_ceiling"], worker_type_definition)
    at_cap = _parse_scope_at_cap(body["at_cap"])
    ticket = tickets_data.accept_proposal(
        conn,
        ticket_id,
        field=field,
        actor=ctx.actor,
        now=now,
        edited_body=body["edited_body"],
        next_ceiling=next_ceiling,
        at_cap=at_cap,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/return-for-revision")
async def return_ticket_for_revision(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
    worker_context: WorkerContext,
) -> JsonDict:
    body = RevisionMessageBody(message=body_str(raw, "message"))
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = await tickets_actions.return_ticket_for_revision(
        conversations,
        worker_context,
        conn,
        ticket_id,
        message=body["message"],
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/notes/{field}")
async def put_notes(
    ticket_id: str, field: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = NoteBody(note=body_opt_str(raw, "note"), user_note=body_opt_str(raw, "user_note"))
    if "note" in raw and "user_note" in raw:
        raise PlannerError(ErrorCode.validation, "use note or user_note, not both", {})
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    now = clk.now_unix()
    ticket = tickets_data.set_note(
        conn,
        ticket_id,
        field=field,
        note=body["user_note"] if "user_note" in raw else body["note"],
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/recap")
async def put_recap(
    ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = RecapBody(body=body_str(raw, "body"))
    now = clk.now_unix()
    ticket = tickets_data.write_recap(conn, ticket_id, body=body["body"], actor=ctx.actor, now=now)
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/value/{field}")
async def put_value(
    ticket_id: str,
    field: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    body = ValueEditBody(body=body_str(raw, "body"))
    require_direct_write(ctx)
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    now = clk.now_unix()
    ticket = tickets_data.edit_field_value(
        conn,
        ticket_id,
        field=field,
        new_body=body["body"],
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/scope")
async def scope_ticket(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    body = ScopeBody(ceiling=body_opt_str(raw, "ceiling"), at_cap=body_opt_str(raw, "at_cap"))
    require_direct_write(ctx)
    now = clk.now_unix()
    ceiling_raw = body["ceiling"]
    at_cap_raw = body["at_cap"]
    if ceiling_raw is None or at_cap_raw is None:
        missing = [
            name
            for name, value in (("ceiling", ceiling_raw), ("at_cap", at_cap_raw))
            if value is None
        ]
        raise PlannerError(
            ErrorCode.scope_missing, "scope requires ceiling and at_cap", {"missing": missing}
        )
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    if ceiling_raw not in worker_type_definition.ceiling_range():
        raise PlannerError(ErrorCode.scope_invalid, "unknown ceiling", {"ceiling": ceiling_raw})
    try:
        at_cap = AtCap(at_cap_raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown at_cap", {"at_cap": at_cap_raw}
        ) from None
    ticket = tickets_data.change_scope(
        conn,
        ticket_id,
        ceiling=ceiling_raw,
        at_cap=at_cap,
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/stage")
async def set_stage(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    body = StageBody(to_stage=body_str(raw, "to_stage"))
    require_direct_write(ctx)
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    to_stage = body["to_stage"]
    # A linear stage of the type, or the reserved dropped (the writer rejects dropped
    # with "use the drop action"); anything else is foreign to the type.
    if to_stage != worker_type_definition.dropped_stage.id:
        worker_type_definition.stage_index(to_stage)
    now = clk.now_unix()
    ticket = tickets_data.set_stage(
        conn,
        ticket_id,
        new_stage=to_stage,
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/drop")
async def drop_ticket(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_data.drop_ticket(
        conn,
        ticket_id,
        actor=ctx.actor,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/takeover")
async def take_over_ticket(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_data.take_over_ticket(
        conn,
        ticket_id,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/release")
async def release_ticket(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_data.release_ticket(
        conn,
        ticket_id,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/request-user-help")
async def request_user_help(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    now = clk.now_unix()
    ticket = tickets_data.request_user_help(
        conn, ticket_id, actor=ctx.actor, now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/stage-ownership/{stage}")
async def put_stage_ownership(
    ticket_id: str,
    stage: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    if set(raw) != {"ownership_mode"}:
        raise PlannerError(
            ErrorCode.validation,
            "stage ownership requires ownership_mode",
            {"fields": sorted(raw)},
        )
    ownership_raw = body_opt_str(raw, "ownership_mode")
    ownership_mode = (
        parse_enum(StageOwnershipMode, ownership_raw, "ownership_mode")
        if ownership_raw is not None
        else None
    )
    now = clk.now_unix()
    ticket = tickets_data.set_stage_ownership(
        conn,
        ticket_id,
        stage=stage,
        ownership_mode=ownership_mode,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.get("/tickets/{ticket_id}/copy-text", response_class=PlainTextResponse)
async def ticket_copy_text(ticket_id: str, conn: DbConn) -> str:
    return tickets_views.copy_text(conn, ticket_id)


@router.post("/links")
async def add_link(
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_direct_write(ctx)
    body = LinkBody(
        from_id=body_str(raw, "from_id"),
        to_id=body_str(raw, "to_id"),
        kind=body_str(raw, "kind"),
    )
    kind = parse_enum(LinkKind, body["kind"], "kind")
    now = clk.now_unix()
    tickets_actions.add_link(
        conn,
        body["from_id"],
        body["to_id"],
        kind,
        now=now,
    )
    return {"from_id": body["from_id"], "to_id": body["to_id"], "kind": kind.value}


@router.delete("/links")
async def remove_link(
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    from_id: str,
    to_id: str,
    kind: str,
) -> JsonDict:
    require_direct_write(ctx)
    kind_enum = parse_enum(LinkKind, kind, "kind")
    now = clk.now_unix()
    tickets_actions.remove_link(
        conn,
        from_id,
        to_id,
        kind_enum,
        now=now,
    )
    return {"ok": True}


@router.get("/board")
async def board(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return tickets_views.board_view(conn, day_id=day_id)


@router.get("/review")
async def review(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return tickets_views.review_view(conn, day_id=day_id)
