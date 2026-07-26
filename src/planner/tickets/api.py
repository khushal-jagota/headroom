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

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationSystem,
    require_conversation_backend_key,
)
from planner.conversation.snapshot import BackendSnapshotService
from planner.conversation.storage import ConversationStore
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
from planner.runtime import conversation_start
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
from planner.worker_context.contracts import WorkerContextService
from planner.worker_settings.service import CHIEF_SETTINGS_KEY
from planner.worker_types.configuration import (
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


def get_conversation_record(request: Request) -> ConversationStore:
    runtime = getattr(request.app.state, "conversation", None)
    store = getattr(runtime, "store", None) if runtime is not None else None
    if store is None:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "the conversation record is unavailable",
            {},
        )
    return cast(ConversationStore, store)


def get_worker_context_service(request: Request) -> WorkerContextService:
    return cast(WorkerContextService, request.app.state.worker_context_service)


DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]
Conversations = Annotated[ConversationSystem, Depends(get_conversation_system)]
ConversationRecord = Annotated[ConversationStore, Depends(get_conversation_record)]
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
    conversation_id = tickets_data.read_ticket(conn, ticket_id).conversation_id
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


def _backend_snapshots(request: Request) -> BackendSnapshotService:
    runtime = getattr(request.app.state, "conversation", None)
    service = getattr(runtime, "backend_snapshots", None) if runtime is not None else None
    if service is None:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "the agent backends are unavailable",
            {},
        )
    return cast(BackendSnapshotService, service)


async def _advertised_launch_options(
    request: Request,
    backend_key: ConversationBackendKey,
    candidate_model: str | None,
) -> tuple[frozenset[str], frozenset[str]]:
    """What this backend can actually be launched as: its models, and the efforts for one.

    Reasoning effort belongs to the model that will run rather than to the backend in
    general — a model that names its own efforts is believed, including when it names
    none, and a model that names nothing takes the backend's list. That is the same rule
    the composer applies in the browser, stated once more here because this is the door a
    saved configuration comes through.
    """
    service = _backend_snapshots(request)
    try:
        snapshot = await service.snapshot(backend_key)
    except Exception as error:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "the agent backends are unavailable",
            {},
        ) from error
    efforts = snapshot.reasoning_effort_options
    for model in snapshot.available_models:
        if model.model_id == candidate_model:
            efforts = model.reasoning_effort_options
            break
    return (
        frozenset(model.model_id for model in snapshot.available_models),
        frozenset(efforts),
    )


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
    if ticket.conversation_id is not None:
        owner = tickets_data.read_ticket_by_conversation_id(conn, ticket.conversation_id)
        if owner.id != ticket.id:
            raise PlannerError(
                ErrorCode.validation,
                "ticket durable session is owned by another ticket",
                {
                    "ticket_id": ticket.id,
                    "conversation_id": ticket.conversation_id,
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
    expected = tickets_data.employee_launch_configuration(
        tickets_data.read_ticket(conn, ticket_id)
    )
    registered_backend = require_conversation_backend_key(body["employee_backend"])
    advertised_models: frozenset[str] | None = None
    reasoning_supported: bool | None = None
    advertised_reasoning_efforts: frozenset[str] | None = None
    candidate = EmployeeLaunchConfiguration(
        employee_backend=registered_backend,
        employee_launch_model=body["employee_launch_model"],
        employee_launch_reasoning_effort=body["employee_launch_reasoning_effort"],
    )
    if registered_backend == expected.employee_backend and candidate != expected:
        advertised_models, advertised_reasoning_efforts = await _advertised_launch_options(
            request,
            registered_backend,
            body["employee_launch_model"],
        )
        reasoning_supported = len(advertised_reasoning_efforts) > 0
    ticket = tickets_data.write_employee_configuration(
        conn,
        ticket_id,
        expected_employee_configuration=expected,
        employee_backend=body["employee_backend"],
        employee_launch_model=body["employee_launch_model"],
        employee_launch_reasoning_effort=body["employee_launch_reasoning_effort"],
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


@router.get("/chief/conversation")
async def read_chief_conversation(conn: DbConn) -> JsonDict:
    """Which conversation the Chief is currently talking in, or none."""
    return {
        "conversation_id": conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
    }


@router.post("/chief/conversation")
async def start_chief_conversation(
    conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    """Start the Chief's conversation. The same door a Ticket has, on the same writers.

    What it starts as comes from the Chief's own managed settings, exactly as a Ticket's
    comes from its worker type and its last choice. A Chief that already has a
    conversation keeps it, for the reason a Ticket does: starting again would leave a
    live conversation nothing could reach.
    """
    require_direct_write(ctx)
    conversation_id = conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
    if conversation_id is None:
        conversation_id = await conversation_start.start_agent_conversation(
            conversations, conn, CHIEF_SETTINGS_KEY, conversation_start.agent_resolve(conn)
        )
    return {"conversation_id": conversation_id}


@router.post("/chief/conversation/reset")
async def reset_chief_conversation(
    conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    """Cut the Chief loose from its conversation. This is what New does."""
    require_direct_write(ctx)
    await conversation_start.reset_agent_conversation(conversations, conn, CHIEF_SETTINGS_KEY)
    return {"conversation_id": None}


@router.post("/tickets/{ticket_id}/conversation")
async def start_ticket_conversation(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    """Start this Ticket's conversation, so a person can talk to it before a step runs.

    The readiness loop starts one when it has a step to send. This is the other door: a
    Ticket nobody has run yet, opened by its owner, who types into it. Both doors reach
    the same writer, so a conversation started by hand is the conversation the loop will
    find and use.

    A Ticket that already has one keeps it. Starting again would leave the conversation it
    is pointing at running with nothing able to reach it.
    """
    require_direct_write(ctx)
    now = clk.now_unix()
    ticket = tickets_data.read_ticket(conn, ticket_id)
    if ticket.conversation_id is None:
        await conversation_start.start_ticket_conversation(
            conversations,
            conn,
            ticket,
            conversation_start.worker_resolve(conn, ticket),
            now=now,
        )
        ticket = tickets_data.read_ticket(conn, ticket_id)
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/conversation/reset")
async def reset_ticket_conversation(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    """Cut this Ticket loose from its conversation. This is what New does.

    The old conversation is killed rather than interrupted — its running turn stops and
    everything it was holding is discarded — and the Ticket stops pointing at it. Nothing
    is started here: the Ticket now has no conversation, which is the state the start door
    above already knows how to answer.
    """
    require_direct_write(ctx)
    now = clk.now_unix()
    await conversation_start.reset_ticket_conversation(conversations, conn, ticket_id, now=now)
    return tickets_views.ticket_json(tickets_data.read_ticket(conn, ticket_id), now)


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


async def add_conversation_row_signals(
    board: JsonDict,
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
) -> JsonDict:
    """Add the three conversation-owned row signals to every card on the board.

    ``agent_working`` is whether the Ticket's conversation has a turn running right now,
    and ``needs_me`` is whether that turn is waiting on a permission ask only the owner
    can answer. ``latest_turn_ended_sequence`` is where that conversation last had a turn
    end — the row's half of the reply mark, which the browser compares against how far
    the reader has got. None of the three is a tickets-domain fact and all are awaited,
    so ``board_view`` cannot answer them and they are added here instead. A Ticket with
    no conversation has no conversation to ask about: the first two read false and the
    third reads 0, which is before every real position.

    The record is asked once for the whole board rather than once per row: it is one
    question about a list, and a list is what the board is.

    This reads and writes nothing but the payload it was handed — no transaction, no
    connection of its own.
    """
    cards = [card for column in board["columns"] for card in column["cards"]]
    latest_turn_ended = await conversation_record.latest_turn_ended_sequences(
        [card["conversation_id"] for card in cards if card["conversation_id"] is not None]
    )
    for card in cards:
        conversation_id = card["conversation_id"]
        card["agent_working"] = (
            await conversation_system.is_running(conversation_id)
            if conversation_id is not None
            else False
        )
        card["needs_me"] = (
            await conversation_system.has_pending_permission_ask(conversation_id)
            if conversation_id is not None
            else False
        )
        card["latest_turn_ended_sequence"] = (
            latest_turn_ended.get(conversation_id, 0) if conversation_id is not None else 0
        )
    return board


@router.get("/board")
async def board(
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return await add_conversation_row_signals(
        tickets_views.board_view(conn, day_id=day_id), conversations, conversation_record
    )


@router.get("/review")
async def review(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return tickets_views.review_view(conn, day_id=day_id)
