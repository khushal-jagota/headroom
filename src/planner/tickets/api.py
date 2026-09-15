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
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from planner.conversation.api import (
    OwnerSendBody,
    conversation_message_content,
    delivery_fate_json,
)
from planner.conversation.backend_state import model_is_enabled
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationSystem,
    require_conversation_backend_key,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.snapshot import BackendSnapshotService
from planner.conversation.storage import ConversationStore
from planner.core.authctx import (
    RequestContext,
    reject_agent_fields,
    request_context,
    require_chief,
    require_direct_write,
    require_ticket_delete,
    require_ticket_worker_write,
)
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    JsonDict,
    LinkKind,
    Principal,
    PrincipalKind,
    Priority,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import resolve_day_id
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.message_delivery import service as message_delivery_service
from planner.message_delivery.contracts import MessageDeliveryResult
from planner.projects import data as projects_data
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import (
    ConversationStartOverrides,
    ConversationStartValues,
)
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
    GuidanceBody,
    LinkBody,
    PendingProposalEditBody,
    ProposeWithRecapBody,
    RecapBody,
    ReconcileTicketFromExternalWorkBody,
    RevisionMessageBody,
    ScopeBody,
    StageOwnershipMode,
    Ticket,
    TicketEdit,
    TicketListFilters,
    TicketStatus,
    ValueEditBody,
)
from planner.worker_context.contracts import WorkerContextService
from planner.worker_settings import service as worker_settings_service
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
    "sprint_id",
    "sprint_item_id",
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


def get_conversation_message_files(request: Request) -> ConversationMessageFiles:
    """The files messages carry, for the send doors that keep a picture's bytes."""
    runtime = getattr(request.app.state, "conversation", None)
    message_files = getattr(runtime, "message_files", None) if runtime is not None else None
    if message_files is None:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "the conversation record is unavailable",
            {},
        )
    return cast(ConversationMessageFiles, message_files)


def get_worker_context_service(request: Request) -> WorkerContextService:
    return cast(WorkerContextService, request.app.state.worker_context_service)


DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]
Conversations = Annotated[ConversationSystem, Depends(get_conversation_system)]
MessageFiles = Annotated[ConversationMessageFiles, Depends(get_conversation_message_files)]
ConversationRecord = Annotated[ConversationStore, Depends(get_conversation_record)]
WorkerContext = Annotated[WorkerContextService, Depends(get_worker_context_service)]


def _ticket_detail_with_worker_settings(
    conn: sqlite3.Connection,
    ticket_id: str,
    now: int,
    config: Config,
) -> JsonDict:
    detail = tickets_views.ticket_detail(conn, ticket_id, now)
    detail["suggested_next_ceiling"] = worker_settings_service.read_worker_settings(
        Path(config.db_path).expanduser().parent,
        configured_worker_type_registry(),
        str(detail["worker_type"]),
    ).suggested_next_ceiling
    return detail


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


async def silence_the_worker_before_deleting(
    conn: sqlite3.Connection,
    conversation_system: ConversationSystem,
    ticket_id: str,
) -> None:
    """Kill the turn of a Ticket that is about to stop existing.

    A deletion that goes ahead over a running Worker is the one path where a turn would
    outlive the row it belongs to: the Ticket's status, its context, and its conversation
    link all go, and the Worker keeps talking into a conversation nothing owns. Killing
    stops that turn and discards every held message, so nothing runs afterwards.

    The conversation record and its history survive, as they do for every deletion. An
    idle conversation, or none at all, means there is nothing to kill.
    """
    conversation_id = tickets_data.read_ticket(conn, ticket_id).conversation_id
    if conversation_id is None:
        return
    await conversation_system.kill(conversation_id)


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


def body_bool(body: JsonDict, key: str, default: bool = False) -> bool:
    raw = body.get(key, default)
    if not isinstance(raw, bool):
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


def _marshal_guidance(raw: dict[str, Any]) -> GuidanceBody:
    if set(raw) != {"body"} or not isinstance(raw["body"], str):
        raise PlannerError(ErrorCode.validation, "guidance requires a string body", {})
    return GuidanceBody(body=raw["body"])


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
        ceiling=body_opt_str(raw, "ceiling"),
        at_cap=body_opt_str(raw, "at_cap"),
    )
    if "employee_backend" in raw:
        body["employee_backend"] = body_str(raw, "employee_backend")
    if "employee_launch_model" in raw:
        body["employee_launch_model"] = body_str(raw, "employee_launch_model")
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
        "employee_launch_model",
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
    if "employee_launch_model" in raw:
        body["employee_launch_model"] = body_str(raw, "employee_launch_model")
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
        next_holder=raw.get("next_holder"),
    )


def _parse_principal(raw: object, field: str) -> Principal | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"kind", "id"}:
        raise PlannerError(
            ErrorCode.validation,
            f"{field} must be a principal object with kind and id",
            {field: raw},
        )
    kind_raw = raw.get("kind")
    principal_id = raw.get("id")
    if not isinstance(kind_raw, str):
        raise PlannerError(
            ErrorCode.validation,
            f"{field} kind must be owner, chief, sprint_item, or ticket",
            {field: raw},
        )
    try:
        kind = PrincipalKind(kind_raw)
    except (TypeError, ValueError):
        raise PlannerError(
            ErrorCode.validation,
            f"{field} kind must be owner, chief, sprint_item, or ticket",
            {field: raw},
        ) from None
    if not isinstance(principal_id, str):
        raise PlannerError(ErrorCode.validation, f"{field} id must be text", {field: raw})
    try:
        return Principal(kind, principal_id)
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, str(exc), {field: raw}) from exc


def _parse_required_principal(raw: object, field: str) -> Principal:
    principal = _parse_principal(raw, field)
    if principal is None:
        raise PlannerError(
            ErrorCode.scope_missing,
            f"approval requires {field}",
            {"missing": [field]},
        )
    return principal


# --- scope marshallers ---------------------------------------------------------


def _parse_next_ceiling(
    raw: str | None, worker_type_definition: WorkerTypeDefinition
) -> str | None:
    if raw is None:
        return None
    if raw == NO_FURTHER:
        return NO_FURTHER
    try:
        return worker_type_definition.resolve_ceiling(raw)
    except PlannerError as exc:
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown next_ceiling", {"next_ceiling": raw}
        ) from exc


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
        parse_enum(Priority, body["priority"], "priority") if body["priority"] is not None else None
    )
    project = projects_data.resolve_project(
        conn,
        project_id=body["project_id"],
        project_name=body["project"],
    )
    ticket = tickets_actions.create_ticket(
        conn,
        title=body["title"],
        principal=ctx.principal,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note=body["kickoff_note"],
        project_id=project.id if project is not None else None,
        sprint_id=body["sprint_id"],
        priority=priority,
        deadline=body["deadline"],
        sprint_item_id=body["sprint_item_id"],
        worker_type=body["worker_type"],
        employee_backend=body.get("employee_backend"),
        employee_launch_model=body.get("employee_launch_model"),
        blocked_by_ticket_ids=body["blocked_by_ticket_ids"],
        planning_now=clk.now(),
        boundary_hour=cfg.boundary_hour,
        sprint_item_id_explicit="sprint_item_id" in raw,
        sprint_id_explicit="sprint_id" in raw,
        stated_ceiling=body["ceiling"],
        stated_at_cap=_parse_scope_at_cap(body["at_cap"]),
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
    priority = parse_enum(Priority, priority_raw, "priority") if priority_raw is not None else None
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
        principal=ctx.principal,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        project_id=project.id if project is not None else None,
        sprint_id=body.get("sprint_id"),
        priority=priority,
        deadline=body.get("deadline"),
        sprint_item_id=body.get("sprint_item_id"),
        worker_type=worker_type,
        employee_backend=body.get("employee_backend"),
        employee_launch_model=body.get("employee_launch_model"),
        blocked_by_ticket_ids=body.get("blocked_by_ticket_ids", []),
        planning_now=clk.now(),
        boundary_hour=cfg.boundary_hour,
        sprint_item_id_explicit="sprint_item_id" in raw,
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
        principal=ctx.principal,
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


@router.get("/ticket-summaries")
async def list_ticket_summaries(
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    stage: Annotated[list[str] | None, Query()] = None,
    exclude_stage: Annotated[list[str] | None, Query()] = None,
    ticket_status: Annotated[list[str] | None, Query()] = None,
    exclude_ticket_status: Annotated[list[str] | None, Query()] = None,
    include_terminal: bool = False,
    search: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
    day: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> JsonDict:
    included_stages = tuple(stage or ())
    registry = configured_worker_type_registry()
    terminal_stages = {
        terminal
        for worker_type in registry.registered_worker_types()
        for terminal in (
            registry.require(worker_type).completed_stage(),
            registry.require(worker_type).dropped_stage.id,
        )
    }
    requested_terminal = sorted(set(included_stages) & terminal_stages)
    if requested_terminal and not include_terminal:
        raise PlannerError(
            ErrorCode.validation,
            "terminal stages require include_terminal",
            {"stages": requested_terminal},
        )
    statuses = tuple(
        parse_enum(TicketStatus, value, "ticket_status") for value in (ticket_status or ())
    )
    excluded_statuses = tuple(
        parse_enum(TicketStatus, value, "exclude_ticket_status")
        for value in (exclude_ticket_status or ())
    )
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    day_id = resolve_day_id(day, clk.now(), cfg.boundary_hour) if day is not None else None
    page = tickets_views.list_ticket_summaries(
        conn,
        page_request=ListPageRequest(limit=limit, offset=offset),
        filters=TicketListFilters(
            stages=included_stages,
            excluded_stages=tuple(exclude_stage or ()),
            ticket_statuses=statuses,
            excluded_ticket_statuses=excluded_statuses,
            include_terminal=include_terminal,
            search=search,
        ),
        project_id=resolved_project.id if resolved_project is not None else None,
        sprint_id=sprint_id,
        sprint_item_id=sprint_item_id,
        day_id=day_id,
    )
    return page.response("tickets")


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


@dataclass(frozen=True)
class ResolvedEmployeeConfiguration:
    """A launch configuration that has passed every check a write can make beforehand.

    Holding it as a value is what lets a caller validate at one moment and write at
    another. The supervisor restart needs exactly that: a configuration it cannot write
    must be refused before the old conversation is killed, or a bad argument would cost
    the Ticket its worker and give it nothing back.
    """

    expected: EmployeeLaunchConfiguration
    employee_backend: str
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None
    advertised_models: frozenset[str] | None
    reasoning_supported: bool | None
    advertised_reasoning_efforts: frozenset[str] | None


async def resolve_employee_configuration(
    request: Request,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    employee_backend: str,
    employee_launch_model: str,
    employee_launch_reasoning_effort: str | None,
) -> ResolvedEmployeeConfiguration:
    """Check a launch configuration against the backend registry and its catalog.

    Every check that can be made without writing is made here: the backend is registered,
    the model is not disabled, and a same-backend change is measured against what that
    backend advertises. A backend change carries no catalog, for the reason
    ``normalize_employee_launch_configuration`` gives — the old backend's catalog is the
    wrong one to ask.
    """
    expected = tickets_data.employee_launch_configuration(tickets_data.read_ticket(conn, ticket_id))
    registered_backend = require_conversation_backend_key(employee_backend)
    advertised_models: frozenset[str] | None = None
    reasoning_supported: bool | None = None
    advertised_reasoning_efforts: frozenset[str] | None = None
    candidate = EmployeeLaunchConfiguration(
        employee_backend=registered_backend,
        employee_launch_model=employee_launch_model,
        employee_launch_reasoning_effort=employee_launch_reasoning_effort,
    )
    if not model_is_enabled(conn, registered_backend, employee_launch_model):
        raise PlannerError(
            ErrorCode.validation,
            "Employee model is disabled",
            {
                "employee_backend": str(registered_backend),
                "employee_launch_model": employee_launch_model,
            },
        )
    if registered_backend == expected.employee_backend and candidate != expected:
        advertised_models, advertised_reasoning_efforts = await _advertised_launch_options(
            request,
            registered_backend,
            employee_launch_model,
        )
        reasoning_supported = len(advertised_reasoning_efforts) > 0
    return ResolvedEmployeeConfiguration(
        expected=expected,
        employee_backend=employee_backend,
        employee_launch_model=employee_launch_model,
        employee_launch_reasoning_effort=employee_launch_reasoning_effort,
        advertised_models=advertised_models,
        reasoning_supported=reasoning_supported,
        advertised_reasoning_efforts=advertised_reasoning_efforts,
    )


def write_resolved_employee_configuration(
    conn: sqlite3.Connection,
    ticket_id: str,
    resolved: ResolvedEmployeeConfiguration,
    *,
    now: int,
) -> Ticket:
    """Write a resolved configuration through the one launch-values door."""
    return tickets_data.write_employee_configuration(
        conn,
        ticket_id,
        expected_employee_configuration=resolved.expected,
        employee_backend=resolved.employee_backend,
        employee_launch_model=resolved.employee_launch_model,
        employee_launch_reasoning_effort=resolved.employee_launch_reasoning_effort,
        advertised_models=resolved.advertised_models,
        reasoning_supported=resolved.reasoning_supported,
        advertised_reasoning_efforts=resolved.advertised_reasoning_efforts,
        now=now,
    )


@router.get("/tickets/{ticket_id}/worker-self")
async def get_worker_self_ticket(
    ticket_id: str,
    conn: DbConn,
    clk: Clk,
    config: Cfg,
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
    detail = _ticket_detail_with_worker_settings(conn, ticket.id, clk.now_unix(), config)
    detail["worker"] = (
        configured_worker_type_registry()
        .require(ticket.worker_type)
        .worker_profile.specialist_skill
    )
    return detail


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, conn: DbConn, clk: Clk, config: Cfg) -> JsonDict:
    return _ticket_detail_with_worker_settings(conn, ticket_id, clk.now_unix(), config)


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
        # The model is read the way the backend is: a null is a body that did not say what
        # this Ticket runs on, and there is nothing here that could answer for it.
        employee_launch_model=body_str(raw, "employee_launch_model"),
        employee_launch_reasoning_effort=body_opt_str(raw, "employee_launch_reasoning_effort"),
    )
    resolved = await resolve_employee_configuration(
        request,
        conn,
        ticket_id,
        employee_backend=body["employee_backend"],
        employee_launch_model=body["employee_launch_model"],
        employee_launch_reasoning_effort=body["employee_launch_reasoning_effort"],
    )
    ticket = write_resolved_employee_configuration(conn, ticket_id, resolved, now=clk.now_unix())
    return _ticket_detail_with_worker_settings(conn, ticket.id, clk.now_unix(), get_config(request))


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
    force: Annotated[bool, Query()] = False,
) -> JsonDict:
    supervisor_sprint_item_id = require_ticket_delete(conn, ctx, ticket_id)
    # A supervisor deletes its own child Ticket outright, and force is how the user reaches
    # the same place. Both walk past the running guards, so the Ticket's turn is killed
    # here instead: a Worker must never outlive the Ticket it belongs to.
    even_while_running = force or supervisor_sprint_item_id is not None
    if even_while_running:
        await silence_the_worker_before_deleting(conn, conversations, ticket_id)
    else:
        await reject_while_the_conversation_is_running(conn, conversations, ticket_id)
    deleted = tickets_data.delete_ticket(
        conn,
        ticket_id,
        principal=ctx.principal,
        now=clk.now_unix(),
        even_while_running=even_while_running,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
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
        "sprint_item_id",
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
    if "sprint_item_id" in body:
        edit["sprint_item_id"] = body_opt_str(body, "sprint_item_id")
    now = clk.now_unix()
    ticket = tickets_data.edit_ticket(
        conn,
        ticket_id,
        edit=edit,
        title_max_chars=TITLE_MAX_CHARS,
        principal=ctx.principal,
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
    if ctx.principal != Principal(PrincipalKind.ticket, ticket_id):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "only the Ticket's own Worker can file its proposal",
            {"ticket_id": ticket_id},
        )
    body = ProposeWithRecapBody(
        body=body_str(raw, "body"),
        recap=body_str(raw, "recap"),
    )
    ticket = tickets_actions.file_current_proposal(
        conn,
        ticket_id,
        body=body["body"],
        recap=body["recap"],
        ctx=ctx,
        clock=clk,
    )
    return tickets_views.ticket_json(ticket, clk.now_unix())


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
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    now = clk.now_unix()
    next_ceiling = _parse_next_ceiling(body["next_ceiling"], worker_type_definition)
    at_cap = _parse_scope_at_cap(body["at_cap"])
    ticket = tickets_data.accept_proposal(
        conn,
        ticket_id,
        field=field,
        principal=ctx.principal,
        now=now,
        edited_body=body["edited_body"],
        next_ceiling=next_ceiling,
        at_cap=at_cap,
        next_holder=_parse_required_principal(body["next_holder"], "next_holder"),
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
) -> JsonDict:
    body = RevisionMessageBody(message=body_str(raw, "message"))
    now = clk.now_unix()
    ticket = await tickets_actions.return_ticket_for_revision(
        conversations,
        conn,
        ticket_id,
        message=body["message"],
        ctx=ctx,
        clock=clk,
    )
    return tickets_views.ticket_json(ticket, now)


def _what_this_message_runs_under(body: OwnerSendBody) -> ConversationStartOverrides:
    """The values a message says it runs on, as the resolver takes them."""
    return ConversationStartOverrides(
        backend_key=(
            None if body.backend_key is None else require_conversation_backend_key(body.backend_key)
        ),
        model=body.model,
        reasoning_effort=body.reasoning_effort,
    )


def _delivered_message_json(
    delivered: conversation_start.DeliveredMessage | MessageDeliveryResult,
) -> JsonDict:
    """The fate, and which conversation it happened in.

    The id is null when a message that was to make a conversation did not land, because
    then there is none: a sender reading null has nothing to open and nothing to hold on to.
    """
    return {
        "conversation_id": delivered.conversation_id,
        **delivery_fate_json(delivered.fate),
    }


def _conversation_start_values_json(values: ConversationStartValues) -> JsonDict:
    """What a start resolved to, as far as anybody looking at it can choose.

    The backend, the model and the reasoning effort, under the names a started
    conversation reports them under — so a panel reads the same three fields whether it is
    asking what a conversation runs on or what one would. The rest of a resolved start is
    nobody's choice: the role an agent is told to be, the folder it runs in and what it
    may reach are the same however the person answers, so nothing shows them.
    """
    return {
        "backend_key": values.backend_key.value,
        "model": values.model,
        "reasoning_effort": values.reasoning_effort,
    }


@router.get("/chief/conversation")
async def read_chief_conversation(conn: DbConn) -> JsonDict:
    """Which conversation the Chief is currently talking in, or none."""
    return {"conversation_id": conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)}


@router.get("/chief/conversation/start-values")
async def read_chief_conversation_start_values(conn: DbConn) -> JsonDict:
    """What a conversation started for the Chief right now would run on.

    This is the question a panel with no conversation has to answer to show anything at
    all, and it is answered by the same resolve the send door runs when a message brings
    one into being — so what a person is shown before they type is what they get. It reads
    the Chief's own managed settings and writes nothing, and asking twice costs nothing.
    """
    return _conversation_start_values_json(conversation_start.agent_resolve(conn))


@router.post("/chief/conversation/send")
async def send_to_chief_conversation(
    body: OwnerSendBody,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
    message_files: MessageFiles,
) -> JsonDict:
    """Send a message to the Chief, making its conversation if there is not one yet.

    The Chief's door has the shape a Ticket's has, and for the same reason: a conversation
    is not something a person makes and then talks into, it is what talking makes.

    The sender's own two facts about the message go through untouched. A browser draws a
    message the moment Enter is pressed and stops drawing it when the record hands it back
    — which it can only recognise by the name it minted. A door that takes the name and
    does not pass it on leaves that browser drawing a message the record already has.
    """
    require_direct_write(ctx)
    created_conversation_id = conversation_start.new_conversation_id()
    runs_under = _what_this_message_runs_under(body)
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clk,
        ctx,
        CHIEF_PRINCIPAL,
        partial(conversation_message_content, message_files, sent=body.content),
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=runs_under,
        mode=body.mode,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
    )
    return _delivered_message_json(delivered)


@router.post("/chief/conversation/reset")
async def reset_chief_conversation(
    conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    """Cut the Chief loose from its conversation. This is what New does."""
    require_direct_write(ctx)
    await conversation_start.reset_agent_conversation(conversations, conn, CHIEF_SETTINGS_KEY)
    return {"conversation_id": None}


@router.get("/tickets/{ticket_id}/conversation/start-values")
async def read_ticket_conversation_start_values(ticket_id: str, conn: DbConn) -> JsonDict:
    """What a conversation started for this Ticket's worker right now would run on.

    The Chief's question, asked of a Ticket: the same resolve the send door runs, so the
    Worker type's launch defaults and whatever this Ticket last ran on answer here exactly
    as they will answer when a message makes the conversation.
    """
    return _conversation_start_values_json(
        conversation_start.worker_resolve(conn, tickets_data.read_ticket(conn, ticket_id))
    )


@router.post("/tickets/{ticket_id}/conversation/send")
async def send_to_ticket_conversation(
    ticket_id: str,
    body: OwnerSendBody,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
    message_files: MessageFiles,
) -> JsonDict:
    """Send a message to this Ticket's worker, making its conversation if there is none.

    This is the whole of talking to a worker. A Ticket nobody has run has no conversation
    at all — not an empty one — and this message is what brings one into being, on the
    values it says it runs under. The readiness loop comes through the same door when it
    has a step to send, so a conversation begun by hand is the one the loop finds.

    The sender's own two facts about the message go through untouched, for the reason the
    Chief's door gives: a browser recognises its own message coming back by the name it
    minted, and a name this door drops is one the record can never hand back.
    """
    require_direct_write(ctx)
    created_conversation_id = conversation_start.new_conversation_id()
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clk,
        ctx,
        Principal(PrincipalKind.ticket, ticket_id),
        partial(conversation_message_content, message_files, sent=body.content),
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=_what_this_message_runs_under(body),
        mode=body.mode,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
    )
    return _delivered_message_json(delivered)


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


@router.put("/tickets/{ticket_id}/guidance")
async def put_guidance(
    ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = _marshal_guidance(raw)
    ticket = tickets_data.replace_guidance(
        conn, ticket_id, body=body["body"], principal=ctx.principal, now=clk.now_unix()
    )
    return tickets_views.ticket_json(ticket, clk.now_unix())


@router.post("/tickets/{ticket_id}/guidance/append")
async def append_guidance(
    ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = _marshal_guidance(raw)
    ticket = tickets_data.append_guidance(
        conn, ticket_id, body=body["body"], principal=ctx.principal, now=clk.now_unix()
    )
    return tickets_views.ticket_json(ticket, clk.now_unix())


@router.put("/tickets/{ticket_id}/recap")
async def put_recap(
    ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = RecapBody(body=body_str(raw, "body"))
    now = clk.now_unix()
    ticket = tickets_data.write_recap(
        conn, ticket_id, body=body["body"], principal=ctx.principal, now=now
    )
    return tickets_views.ticket_json(ticket, now)


@router.put("/tickets/{ticket_id}/proposal")
async def edit_pending_proposal(
    ticket_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    body = PendingProposalEditBody(field=body_str(raw, "field"), body=body_str(raw, "body"))
    now = clk.now_unix()
    ticket = tickets_data.edit_pending_proposal(
        conn,
        ticket_id,
        field=body["field"],
        new_body=body["body"],
        principal=ctx.principal,
        now=now,
    )
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
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    now = clk.now_unix()
    ticket = tickets_data.edit_field_value(
        conn,
        ticket_id,
        field=field,
        new_body=body["body"],
        principal=ctx.principal,
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
            ErrorCode.scope_missing,
            "scope requires ceiling and at_cap",
            {"missing": missing},
        )
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
        principal=ctx.principal,
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
        principal=ctx.principal,
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
        conn,
        ticket_id,
        principal=ctx.principal,
        now=now,
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
        admit=lambda: require_ticket_worker_write(conn, ctx),
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
    kind_enum = parse_enum(LinkKind, kind, "kind")
    now = clk.now_unix()
    tickets_actions.remove_link(
        conn,
        from_id,
        to_id,
        kind_enum,
        now=now,
        admit=lambda: require_ticket_worker_write(conn, ctx),
    )
    return {"ok": True}


async def add_conversation_row_signals(
    board: JsonDict,
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
) -> JsonDict:
    """Add the live conversation-owned row signals to every row on the board.

    A row is a card or a Sprint Item, and both carry a ``conversation_id``. A card's
    conversation belongs to its Ticket's worker, and an Item's belongs to its own
    supervisor.

    ``agent_working`` is whether that conversation has a turn running right now, and
    ``needs_me`` is whether that turn is waiting on a permission decision or answers only
    the owner can give. Both are asked for every row.

    ``latest_turn_ended_sequence`` is where the conversation last had a turn end, and it
    is a row's half of the unread-reply mark. Every row is asked, because an Item
    conversation replies only to something the user said.

    None of these signals is a tickets-domain fact and all are awaited, so ``board_view``
    cannot answer them. A row with no conversation has no conversation to ask about, so
    the first two read false and the third reads 0, which is before every real position.

    The record is asked once for the whole board rather than once per row: it is one
    question about a list, and a list is what the board is.

    This reads and writes nothing but the payload it was handed — no transaction, no
    connection of its own.
    """
    cards = [card for column in board["columns"] for card in column["cards"]]
    rows = [*cards, *board["sprint_items"]]
    latest_turn_ended = await conversation_record.latest_turn_ended_sequences(
        [row["conversation_id"] for row in rows if row["conversation_id"] is not None]
    )
    owner_read_through = await conversation_record.owner_read_through_sequences(
        [row["conversation_id"] for row in rows if row["conversation_id"] is not None]
    )
    for row in rows:
        conversation_id = row["conversation_id"]
        row["agent_working"] = (
            await conversation_system.is_running(conversation_id)
            if conversation_id is not None
            else False
        )
        row["needs_me"] = (
            (
                await conversation_system.has_pending_permission_ask(conversation_id)
                or await conversation_system.has_pending_user_input(conversation_id)
            )
            if conversation_id is not None
            else False
        )
        row["latest_turn_ended_sequence"] = (
            latest_turn_ended.get(conversation_id, 0) if conversation_id is not None else 0
        )
        row["owner_read_through_sequence"] = (
            owner_read_through.get(conversation_id, 0) if conversation_id is not None else 0
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
        tickets_views.board_view(conn, day_id=day_id),
        conversations,
        conversation_record,
    )


@router.get("/review")
async def review(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return tickets_views.review_view(conn, day_id=day_id)
