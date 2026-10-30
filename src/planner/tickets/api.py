"""Ticket routes (§9), plus Ticket blocks and the ticket-centric
derived views (board, Review). Thin HTTP shells over the stage-3 writers and the
pure read views: every handler is parse -> auth -> writer -> serialize. No route
re-implements a domain rule.

This module also homes the shared request plumbing (config/clock accessors, the
per-request connection dependency, the transaction context manager, and the enum
and body-key marshallers). The other api modules import these from here; the direction
is strictly one-way (domain API -> tickets API), and views modules
import no api module at all.

Request bodies arrive as plain dicts and are marshalled into the TypedDict shapes
declared in each domain's contracts (§14); a null or wrong-typed key raises the
validation envelope rather than FastAPI's 422, so the CLI and UI see one error shape."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import TypeAdapter

from planner.conversation.api import (
    OwnerSendBody,
    conversation_message_content,
    delivery_fate_json,
    video_send_admission,
)
from planner.conversation.backend_state import require_enabled_model
from planner.conversation.contracts import (
    ConversationSystem,
    require_conversation_backend_key,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    ModelSelection,
    SelectionKind,
)
from planner.conversation.storage import ConversationStore
from planner.conversation.video_uploads.contracts import UploadDestination
from planner.core import authority
from planner.core.authctx import (
    RequestContext,
    request_context,
)
from planner.core.authority import require_above, require_above_or_self, require_self
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    JsonDict,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.list_reads.detail import (
    ReadDetail,
    parse_read_detail,
    reject_parameters,
    require_full_for_one,
)
from planner.managers.assignment import normalize_assignment
from planner.message_delivery import service as message_delivery_service
from planner.message_delivery.contracts import MessageDeliveryResult, MessageRecordedToOwner
from planner.projects import data as projects_data
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import (
    ConversationStartOverrides,
    ConversationStartValues,
)
from planner.runtime.worker_step_readiness_loop import (
    WorkerStepStartResult,
    start_ready_worker_step,
)
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets import worker_restart
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AcceptBody,
    CreateTicketBody,
    EmployeeConfigurationBody,
    EmployeeLaunchConfiguration,
    GateCompletionBody,
    ProposalBody,
    RejectionBody,
    ReviewTransferNotice,
    Ticket,
    TicketCreationContextRead,
    TicketEdit,
    TicketListFilters,
    TicketLocaleChoice,
    TicketStatus,
)
from planner.tickets.logic import admission
from planner.tickets.review_transfer import notify_review_transfers
from planner.work_attention import add_work_attention, read_running_conversation_count
from planner.worker_settings.service import CHIEF_SETTINGS_KEY
from planner.worker_types.configuration import (
    configured_worker_type_registry,
)
from planner.worker_types.contracts import WorkerTypeDefinition

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


DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]
Conversations = Annotated[ConversationSystem, Depends(get_conversation_system)]
MessageFiles = Annotated[ConversationMessageFiles, Depends(get_conversation_message_files)]
ConversationRecord = Annotated[ConversationStore, Depends(get_conversation_record)]


def _ticket_detail(
    conn: sqlite3.Connection,
    ticket_id: str,
    now: int,
) -> JsonDict:
    return tickets_views.ticket_detail(conn, ticket_id, now)


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
    """Wrap a NON-self-transacting writer (Ticket blocks) so a mid-sequence
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
# real routes. Reserved bookends (needs_brief/done) are shared by every
# Worker type (PLAN invariant 1); the Worker-type-specific middle Stages/fields are not.


def _require_create_worker_type(raw: JsonDict) -> str:
    """A create's Worker type, required and known before any writer runs."""
    worker_type = body_str(raw, "worker_type")
    known = configured_worker_type_registry().supported_worker_types()
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


def _marshal_settled_field_values(
    conn: sqlite3.Connection, ticket_id: str, raw: object
) -> dict[str, str]:
    """Settled field edits, checked against the Ticket's own Worker type before any write."""
    if not isinstance(raw, dict) or not raw:
        raise PlannerError(ErrorCode.validation, "field_values requires a non-empty object", {})
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    values: dict[str, str] = {}
    for field, body in raw.items():
        if not isinstance(body, str):
            raise PlannerError(
                ErrorCode.validation, "a field value must be a string", {"field": field}
            )
        _validate_field(worker_type_definition, str(field))
        values[str(field)] = body
    return values


def _marshal_create_ticket(raw: JsonDict, *, draft: bool = False) -> CreateTicketBody:
    raw = normalize_assignment(raw)
    obsolete = set(raw) & {"sprint_id", "manager_id", "sprint_item_id", "day", "day_id"}
    if obsolete:
        raise PlannerError(
            ErrorCode.validation, "use manager assignment instead of calendar placement"
        )
    for key in raw:
        if key not in CreateTicketBody.__annotations__:
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": key})
    body = CreateTicketBody(
        worker_type=_require_create_worker_type(raw),
        title="" if draft else body_str(raw, "title"),
        brief="" if draft else body_str(raw, "brief"),
        deadline=body_opt_str(raw, "deadline"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
        manager=raw.get("manager"),
        blocked_by_ticket_ids=body_str_list(raw, "blocked_by_ticket_ids"),
        ceiling=body_opt_str(raw, "ceiling"),
        ceiling_holder=raw.get("ceiling_holder"),
    )
    if "selection_kind" in raw:
        try:
            body["selection_kind"] = SelectionKind(body_str(raw, "selection_kind")).value
        except ValueError as error:
            raise PlannerError(ErrorCode.validation, "unknown selection_kind", {}) from error
    if "locale" in raw:
        from planner.tickets.logic.locale import require_ticket_locale

        body["locale"] = require_ticket_locale(raw["locale"]).value
    if "employee_backend" in raw:
        body["employee_backend"] = body_str(raw, "employee_backend")
    if "employee_launch_model" in raw:
        body["employee_launch_model"] = body_str(raw, "employee_launch_model")
    if "employee_launch_reasoning_effort" in raw:
        body["employee_launch_reasoning_effort"] = body_opt_str(
            raw, "employee_launch_reasoning_effort"
        )
    return body


def _marshal_accept(raw: JsonDict) -> AcceptBody:
    revision = raw.get("expected_proposal_revision")
    if revision is not None and (type(revision) is not int or revision <= 0):
        raise PlannerError(
            ErrorCode.validation, "expected_proposal_revision must be a positive integer", {}
        )
    return AcceptBody(
        expected_proposal_revision=revision,
        edited_body=body_opt_str(raw, "edited_body"),
        next_ceiling=body_opt_str(raw, "next_ceiling"),
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
            f"{field} kind must be owner, chief, manager, or ticket",
            {field: raw},
        )
    try:
        kind = PrincipalKind(kind_raw)
    except (TypeError, ValueError):
        raise PlannerError(
            ErrorCode.validation,
            f"{field} kind must be owner, chief, manager, or ticket",
            {field: raw},
        ) from None
    if not isinstance(principal_id, str):
        raise PlannerError(ErrorCode.validation, f"{field} id must be text", {field: raw})
    try:
        return Principal(kind, principal_id)
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, str(exc), {field: raw}) from exc


def _parse_manager(raw: object) -> Principal:
    manager = _parse_principal(raw, "manager")
    if manager is None:
        raise PlannerError(ErrorCode.validation, "manager must name Chief or a Manager")
    admission.validate_manager(manager)
    return manager


def _parse_stated_holder(raw: object, field: str) -> Principal:
    """A holder named on an edit. Unlike approval, there is nothing to fall back to."""
    principal = _parse_principal(raw, field)
    if principal is None:
        raise PlannerError(
            ErrorCode.validation,
            f"{field} must be a principal object with kind and id",
            {field: raw},
        )
    return principal


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


# --- ticket routes -------------------------------------------------------------


def resolve_ticket_creation_selection(
    raw: JsonDict,
    conn: sqlite3.Connection,
    *,
    principal: Principal | None = None,
) -> tuple[ModelSelection, str | None]:
    """Resolve authored draft intent and workspace authority without observation."""
    body = _marshal_create_ticket(raw, draft=True)
    manager = (
        _parse_manager(body["manager"])
        if "manager" in raw or "orchestrator" in raw
        else CHIEF_PRINCIPAL
    )
    if principal is not None:
        manager_id = manager.id if manager.kind is PrincipalKind.manager else None
        if manager_id is not None:
            authority.require_in_chain(conn, principal, authority.manager(manager_id))
        if body["ceiling"] is not None or body["ceiling_holder"] is not None:
            authority.require_above_a_ticket_being_created(
                conn,
                principal,
                ticket_id="uncreated-validation-target",
                parent_manager_id=manager_id,
            )
    project = projects_data.resolve_project(
        conn,
        project_id=body["project_id"],
        project_name=body["project"],
    )
    context = tickets_data.resolve_ticket_draft_context(
        conn,
        worker_type=body["worker_type"],
        locale=body.get("locale"),
        employee_backend=body.get("employee_backend"),
        employee_launch_model=body.get("employee_launch_model"),
        employee_launch_reasoning_effort=body.get("employee_launch_reasoning_effort"),
        reasoning_effort_is_set="employee_launch_reasoning_effort" in body,
        project_id=project.id if project is not None else None,
        manager_id=manager.id if manager.kind is PrincipalKind.manager else None,
        deadline=body["deadline"],
        blocked_by_ticket_ids=body["blocked_by_ticket_ids"],
    )
    launch = context.launch_configuration
    assert launch.employee_launch_model is not None
    selection = ModelSelection(
        context.locale,
        require_conversation_backend_key(launch.employee_backend),
        launch.employee_launch_model,
        SelectionKind(body.get("selection_kind", SelectionKind.legacy_unclassified)),
        launch.employee_launch_reasoning_effort,
        "draft",
    )
    return selection, context.project_id


async def validate_ticket_creation(
    raw: JsonDict,
    conn: sqlite3.Connection,
    system: ConversationSystem,
    *,
    principal: Principal | None = None,
) -> CanonicalModelValidation:
    return resolve_ticket_creation_validation(raw, conn, principal=principal)


def resolve_ticket_creation_validation(
    raw: JsonDict, conn: sqlite3.Connection, *, principal: Principal | None = None
) -> CanonicalModelValidation:
    """Resolve creation policy using only the caller's database transaction."""
    from planner.conversation.launch_intent import bind_model_intent

    selection, _ = resolve_ticket_creation_selection(raw, conn, principal=principal)
    require_enabled_model(conn, selection.backend_key, selection.submitted_token)
    validation = bind_model_intent(selection)
    if validation.launch_token is not None:
        require_enabled_model(conn, selection.backend_key, validation.launch_token)
    return validation


def write_create_ticket(
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    *,
    validation: CanonicalModelValidation | None = None,
) -> JsonDict:
    """Apply creation through the same writer inside HTTP or executor transactions."""
    body = _marshal_create_ticket(raw)
    now = clk.now_unix()
    manager = (
        _parse_manager(body["manager"])
        if "manager" in raw or "orchestrator" in raw
        else Principal(PrincipalKind.chief, "chief")
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
        brief=body["brief"],
        project_id=project.id if project is not None else None,
        deadline=body["deadline"],
        manager_id=(manager.id if manager.kind is PrincipalKind.manager else None),
        manager_kind=manager.kind.value,
        worker_type=body["worker_type"],
        locale=body.get("locale"),
        employee_backend=body.get("employee_backend"),
        employee_launch_model=body.get("employee_launch_model"),
        employee_launch_reasoning_effort=body.get("employee_launch_reasoning_effort"),
        reasoning_effort_is_set="employee_launch_reasoning_effort" in body,
        selection_kind=(
            body.get("selection_kind")
            if validation is None
            else validation.selection.selection_kind.value
        ),
        validation=validation,
        blocked_by_ticket_ids=body["blocked_by_ticket_ids"],
        stated_ceiling=body["ceiling"],
        stated_holder=_parse_principal(body["ceiling_holder"], "ceiling_holder"),
    )
    return tickets_views.ticket_creation_json(conn, ticket, now)


@router.post("/tickets/creation-context")
async def read_ticket_creation_context(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, request: Request
) -> TicketCreationContextRead:
    """Resolve a partial draft without creating state or requiring final text."""
    from planner.tickets.contracts import TicketLocale
    from planner.tickets.locale_configuration import require_locale_backend_compatibility

    # The existing marshaller owns draft input validation as well as final creation.
    # Text is irrelevant to this projection and is never passed to its resolver.
    body = _marshal_create_ticket(raw, draft=True)
    manager = (
        _parse_manager(body["manager"])
        if "manager" in raw or "orchestrator" in raw
        else Principal(PrincipalKind.chief, "chief")
    )
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"]
    )
    context = tickets_data.resolve_ticket_draft_context(
        conn,
        worker_type=body["worker_type"],
        locale=body.get("locale"),
        employee_backend=body.get("employee_backend"),
        employee_launch_model=body.get("employee_launch_model"),
        employee_launch_reasoning_effort=body.get("employee_launch_reasoning_effort"),
        reasoning_effort_is_set="employee_launch_reasoning_effort" in body,
        project_id=project.id if project is not None else None,
        manager_id=manager.id if manager.kind is PrincipalKind.manager else None,
        deadline=body["deadline"],
        blocked_by_ticket_ids=body["blocked_by_ticket_ids"],
    )
    choices: list[TicketLocaleChoice] = []
    for locale in TicketLocale:
        reason = None
        try:
            require_locale_backend_compatibility(
                locale, context.launch_configuration.employee_backend
            )
        except PlannerError as error:
            reason = error.message
        choices.append({"locale": locale.value, "compatible": reason is None, "reason": reason})
    return {
        "resolved_locale": context.locale.value,
        "employee_backend": context.launch_configuration.employee_backend,
        "project_id": context.project_id,
        "locale_choices": choices,
        "launch_validation": TypeAdapter(CanonicalModelValidation).dump_python(
            await validate_ticket_creation(
                raw, conn, get_conversation_system(request), principal=ctx.principal
            ),
            mode="json",
        ),
    }


@router.post("/tickets")
async def create_ticket(
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    request: Request,
) -> JsonDict:
    validation = await validate_ticket_creation(
        raw, conn, get_conversation_system(request), principal=ctx.principal
    )
    conversation_start.refuse_known_incompatibility(validation)
    return write_create_ticket(raw, conn, ctx, clk, validation=validation)


@router.get("/tickets")
async def read_tickets(
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
    detail: str | None = None,
    object_id: Annotated[str | None, Query(alias="id")] = None,
    stage: Annotated[list[str] | None, Query()] = None,
    exclude_stage: Annotated[list[str] | None, Query()] = None,
    ticket_status: Annotated[list[str] | None, Query()] = None,
    exclude_ticket_status: Annotated[list[str] | None, Query()] = None,
    is_backlogged: bool | None = None,
    worker_type: Annotated[list[str] | None, Query()] = None,
    include_terminal: bool = False,
    search: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    manager_id: str | None = None,
    manager_kind: str | None = None,
    sprint_item_id: Annotated[str | None, Query(include_in_schema=False)] = None,
    orchestrator_kind: Annotated[str | None, Query(include_in_schema=False)] = None,
    orchestrator_id: Annotated[str | None, Query(include_in_schema=False)] = None,
    limit: int | None = None,
    offset: int | None = None,
) -> JsonDict:
    """Read Tickets at the level the caller asks for, or one Ticket by id."""
    for legacy_id in (sprint_item_id, orchestrator_id):
        if legacy_id is not None:
            if manager_id is not None and manager_id != legacy_id:
                raise PlannerError(ErrorCode.validation, "conflicting Manager filters")
            manager_id = legacy_id
    if manager_kind == "sprint_item":
        manager_kind = "manager"
    if orchestrator_kind is not None:
        previous_kind = "manager" if orchestrator_kind == "sprint_item" else orchestrator_kind
        if manager_kind is not None and manager_kind != previous_kind:
            raise PlannerError(ErrorCode.validation, "conflicting Manager filters")
        manager_kind = previous_kind
    level = parse_read_detail(detail)
    scope = {
        "stage": stage,
        "exclude_stage": exclude_stage,
        "ticket_status": ticket_status,
        "exclude_ticket_status": exclude_ticket_status,
        "is_backlogged": is_backlogged,
        "worker_type": worker_type,
        "include_terminal": include_terminal or None,
        "search": search,
        "project": project,
        "project_id": project_id,
        "manager_id": manager_id,
        "manager_kind": manager_kind,
        "limit": limit,
        "offset": offset,
    }
    if object_id is not None:
        require_full_for_one(level)
        reject_parameters("id", scope)
        one = _ticket_detail(conn, object_id, clk.now_unix())
        await add_work_attention(conn, conversations, conversation_record, tickets=(one,))
        return one

    # Stage is stored canonical data. Reading compares it directly and does not need
    # to resolve or interpret the Ticket's Worker type.
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )

    if level is ReadDetail.full:
        reject_parameters(
            "detail=full",
            {
                "manager_kind": manager_kind,
                "manager_id": manager_id,
                "is_backlogged": is_backlogged,
                "worker_type": worker_type,
                "exclude_stage": exclude_stage,
                "ticket_status": ticket_status,
                "exclude_ticket_status": exclude_ticket_status,
                "include_terminal": include_terminal or None,
                "search": search,
                "limit": limit,
                "offset": offset,
            },
        )
        if stage is not None and len(stage) > 1:
            raise PlannerError(
                ErrorCode.validation,
                "detail=full takes at most one stage",
                {"stages": list(stage)},
            )
        rows = tickets_views.list_tickets(
            conn,
            clk.now_unix(),
            stage=stage[0] if stage else None,
            project_id=resolved_project.id if resolved_project is not None else None,
            manager_id=manager_id,
        )
        await add_work_attention(conn, conversations, conversation_record, tickets=rows)
        return {"tickets": rows}

    manager = None
    if manager_kind is not None or manager_id is not None:
        manager = _parse_manager(
            {
                "kind": manager_kind or "manager",
                "id": ("chief" if manager_kind == "chief" and manager_id is None else manager_id),
            }
        )

    included_stages = tuple(stage or ())
    registry = configured_worker_type_registry()
    terminal_stages = {
        registry.require(worker_type).completed_stage()
        for worker_type in registry.registered_worker_types()
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
    page = tickets_views.list_ticket_summaries(
        conn,
        page_request=ListPageRequest(
            limit=DEFAULT_LIST_LIMIT if limit is None else limit,
            offset=0 if offset is None else offset,
        ),
        filters=TicketListFilters(
            manager=manager,
            stages=included_stages,
            excluded_stages=tuple(exclude_stage or ()),
            ticket_statuses=statuses,
            excluded_ticket_statuses=excluded_statuses,
            is_backlogged=is_backlogged,
            worker_types=tuple(worker_type or ()),
            include_terminal=include_terminal,
            search=search,
        ),
        project_id=resolved_project.id if resolved_project is not None else None,
        manager_id=manager_id,
    )
    await add_work_attention(conn, conversations, conversation_record, tickets=page.rows)
    return page.response("tickets")


@dataclass(frozen=True)
class ResolvedEmployeeConfiguration:
    """A launch configuration that has passed every check a write can make beforehand.

    Holding it as a value is what lets a caller validate at one moment and write at
    another. The Manager restart needs exactly that: a configuration it cannot write
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
    validation: CanonicalModelValidation | None
    selection_kind: SelectionKind


async def resolve_employee_configuration(
    request: Request,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    employee_backend: str,
    employee_launch_model: str,
    employee_launch_reasoning_effort: str | None,
    selection_kind: SelectionKind | None = None,
) -> ResolvedEmployeeConfiguration:
    """Keep submitted intent and distinguish a known mismatch from absent evidence."""
    ticket = tickets_data.read_ticket(conn, ticket_id)
    expected = tickets_data.employee_launch_configuration(ticket)
    registered_backend = require_conversation_backend_key(employee_backend)
    require_enabled_model(conn, registered_backend, employee_launch_model)
    from planner.tickets.validation.data import read_launch_selection

    prior = None if ticket.employee_launch_model is None else read_launch_selection(conn, ticket_id)
    if selection_kind is None:
        if (
            prior is not None
            and registered_backend == prior.backend_key
            and employee_launch_model == prior.submitted_token
        ):
            selection_kind = prior.selection_kind
        else:
            selection_kind = SelectionKind.legacy_unclassified
    validation = None
    if registered_backend.value != "hermes":
        validation = await conversation_start.verify_ticket_launch(
            get_conversation_system(request),
            conn,
            ticket,
            employee_backend=registered_backend.value,
            employee_launch_model=employee_launch_model,
            employee_launch_reasoning_effort=employee_launch_reasoning_effort,
            selection_kind=selection_kind,
            classify_new=(
                prior is None
                or employee_launch_model != prior.submitted_token
                or registered_backend != prior.backend_key
            ),
            persist=False,
            now=get_clock(request).now_unix(),
        )
        conversation_start.refuse_known_incompatibility(validation)
    return ResolvedEmployeeConfiguration(
        expected=expected,
        employee_backend=registered_backend.value,
        employee_launch_model=employee_launch_model,
        employee_launch_reasoning_effort=employee_launch_reasoning_effort,
        advertised_models=None,
        reasoning_supported=None,
        advertised_reasoning_efforts=None,
        validation=validation,
        selection_kind=(
            selection_kind if validation is None else validation.selection.selection_kind
        ),
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
        selection_kind=resolved.selection_kind.value,
        validation=resolved.validation,
        now=now,
    )


@router.get("/tickets/{ticket_id}/worker-self")
async def get_worker_self_ticket(
    ticket_id: str,
    conn: DbConn,
    clk: Clk,
) -> JsonDict:
    """A worker agent's own Ticket, resolved from `PLAN_TICKET_ID` (its spawn env, flag-on).

    Unlike the ordinary `GET /tickets?detail=full&id=` read, this worker-identity route applies
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
    detail = _ticket_detail(conn, ticket.id, clk.now_unix())
    detail["worker"] = (
        configured_worker_type_registry()
        .require(ticket.worker_type)
        .worker_profile.specialist_skill
    )
    return detail


@router.put("/tickets/{ticket_id}/employee-configuration")
async def put_ticket_employee_configuration(
    ticket_id: str,
    raw: dict[str, Any],
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    link_lock = conversation_start.conversation_link_lock(f"ticket:{ticket_id}")
    if link_lock.locked():
        raise PlannerError(
            ErrorCode.already_running,
            "the Ticket configuration cannot change during a conversation operation",
            {"ticket_id": ticket_id},
        )
    async with link_lock:
        return await _put_ticket_employee_configuration(ticket_id, raw, request, conn, ctx, clk)


async def _put_ticket_employee_configuration(
    ticket_id: str,
    raw: dict[str, Any],
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    required_keys = {
        "employee_backend",
        "employee_launch_model",
        "employee_launch_reasoning_effort",
    }
    if set(raw) - {"selection_kind"} != required_keys:
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
        selection_kind=(
            SelectionKind(body_str(raw, "selection_kind")) if "selection_kind" in raw else None
        ),
    )
    ticket = write_resolved_employee_configuration(conn, ticket_id, resolved, now=clk.now_unix())
    return _ticket_detail(conn, ticket.id, clk.now_unix())


@router.post("/tickets/{ticket_id}/restart-worker")
async def restart_ticket_worker(
    ticket_id: str,
    raw: dict[str, Any],
    request: Request,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    """Start this Ticket's worker step again, optionally on a named configuration.

    New on Khushal's surface. No ordinary route did this: reset and re-configure exist
    separately, and neither releases the claim and starts. Only a Manager could
    reach it, through a wrapper of its own.

    The configuration is resolved before the restart, so a backend or model this Ticket
    cannot launch on is a plain refusal rather than a killed conversation. Position is
    proved before even that: resolving asks the backends what they offer, and no caller
    reaches a question about a Ticket it does not stand above.
    """
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    backend = body_opt_str(raw, "employee_backend")
    model = body_opt_str(raw, "employee_launch_model")
    if (backend is None) != (model is None):
        raise PlannerError(
            ErrorCode.validation,
            "a launch configuration needs both a backend and a model",
            {},
        )
    prepare_configuration: (
        Callable[[], Awaitable[Callable[[sqlite3.Connection, int], None]]] | None
    ) = None
    if backend is not None and model is not None:
        selected_backend, selected_model = backend, model

        async def prepare() -> Callable[[sqlite3.Connection, int], None]:
            resolved = await resolve_employee_configuration(
                request,
                conn,
                ticket_id,
                employee_backend=selected_backend,
                employee_launch_model=selected_model,
                employee_launch_reasoning_effort=body_opt_str(
                    raw, "employee_launch_reasoning_effort"
                ),
                selection_kind=(
                    SelectionKind(body_str(raw, "selection_kind"))
                    if "selection_kind" in raw
                    else None
                ),
            )

            def write(open_conn: sqlite3.Connection, now: int) -> None:
                write_resolved_employee_configuration(open_conn, ticket_id, resolved, now=now)

            return write

        prepare_configuration = prepare

    async def start_worker_step() -> WorkerStepStartResult:
        return await start_ready_worker_step(
            ticket_id,
            connect_database=lambda: connect(cfg.db_path, cfg.db_busy_timeout_ms),
            conversation_system=conversations,
            worker_type_registry=configured_worker_type_registry(),
            now=clk.now_unix,
        )

    return await worker_restart.restart_worker(
        conversations,
        conn,
        ctx.principal,
        ticket_id,
        write_employee_configuration=None,
        prepare_employee_configuration=prepare_configuration,
        start_worker_step=start_worker_step,
        now=clk.now_unix,
    )


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(
    ticket_id: str,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
    force: Annotated[bool, Query()] = False,
) -> JsonDict:
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    # A Manager deletes its own child Ticket outright, and force is how the user reaches
    # the same place. Both walk past the running guards, so the Ticket's turn is killed
    # here instead: a Worker must never outlive the Ticket it belongs to.
    even_while_running = force or ctx.principal.kind is PrincipalKind.manager
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
    )
    return {
        "ok": True,
        "ticket_id": deleted.ticket_id,
        "title": deleted.title,
        "manager_ids": list(deleted.manager_ids),
        "linked_ticket_ids": list(deleted.linked_ticket_ids),
    }


def write_patch_ticket(
    ticket_id: str,
    body: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    *,
    review_transfer_notices: list[ReviewTransferNotice] | None = None,
) -> JsonDict:
    body = normalize_assignment(body)
    recognized = (
        "title",
        "deadline",
        "project",
        "project_id",
        "manager",
        "recap",
        "field_values",
        "ceiling",
        "ceiling_holder",
    )
    for key in body:
        if key not in recognized:
            raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": key})
    if not body:
        raise PlannerError(ErrorCode.validation, "no ticket fields to update", {})
    # The floor: a stranger is refused here, before any of this body is looked at. Which
    # of these fields need a caller above the Ticket rather than the Ticket itself is
    # edit_ticket's question, asked once, where the write happens.
    require_above_or_self(conn, ctx.principal, authority.ticket(ticket_id))

    edit = TicketEdit()
    if "title" in body:
        edit["title"] = body_str(body, "title")
    if "deadline" in body:
        edit["deadline"] = body_opt_str(body, "deadline")

    if "project" in body or "project_id" in body:
        project_raw = body_opt_str(body, "project")
        project_id_raw = body_opt_str(body, "project_id")
        project = projects_data.resolve_project(
            conn, project_id=project_id_raw, project_name=project_raw
        )
        edit["project_id"] = project.id if project is not None else None
    if "manager" in body:
        edit["manager"] = _parse_manager(body["manager"])
    if "recap" in body:
        edit["recap"] = body_str(body, "recap")
    if "ceiling" in body:
        edit["ceiling"] = body_str(body, "ceiling")
    if "ceiling_holder" in body:
        edit["ceiling_holder"] = _parse_stated_holder(body["ceiling_holder"], "ceiling_holder")
    if "field_values" in body:
        edit["field_values"] = _marshal_settled_field_values(conn, ticket_id, body["field_values"])
    now = clk.now_unix()
    notices: list[ReviewTransferNotice] = []
    ticket = tickets_data.edit_ticket(
        conn,
        ticket_id,
        edit=edit,
        title_max_chars=TITLE_MAX_CHARS,
        principal=ctx.principal,
        now=now,
        review_transfer_notices=notices,
    )
    if review_transfer_notices is not None:
        review_transfer_notices.extend(notices)
    return tickets_views.ticket_json(ticket, now)


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(
    ticket_id: str,
    body: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    notices: list[ReviewTransferNotice] = []
    result = write_patch_ticket(ticket_id, body, conn, ctx, clk, review_transfer_notices=notices)
    await notify_review_transfers(conversations, conn, notices, now=clk.now_unix())
    return result


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
    body = ProposalBody(body=body_str(raw, "body"))
    ticket = tickets_actions.file_current_proposal(
        conn,
        ticket_id,
        body=body["body"],
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
    ticket = tickets_data.accept_proposal(
        conn,
        ticket_id,
        field=field,
        principal=ctx.principal,
        now=now,
        edited_body=body["edited_body"],
        next_ceiling=next_ceiling,
        expected_proposal_revision=body["expected_proposal_revision"],
        next_holder=_parse_required_principal(body["next_holder"], "next_holder"),
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/reject")
async def reject_ticket_proposal(
    ticket_id: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    """Send a parked proposal back, with guidance for the executing agent or without."""
    body = RejectionBody(message=body_opt_str(raw, "message"))
    now = clk.now_unix()
    ticket = await tickets_actions.reject_ticket_proposal(
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
        reasoning_effort_is_set="reasoning_effort" in body.model_fields_set,
        selection_kind=body.selection_kind,
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
    """Expose choices and their canonical workspace for advisory-view invalidation."""
    return {
        "backend_key": values.backend_key.value,
        "model": values.model,
        "reasoning_effort": values.reasoning_effort,
        "workspace_folder": str(values.workspace_folder),
        "workspace_path_policy": values.workspace_path_policy.value,
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
    request: Request,
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
    require_above(conn, ctx.principal, authority.owner_only("chief conversation"))
    if body.selection_kind is not None:
        raise PlannerError(
            ErrorCode.validation,
            "explicit selection_kind requires a Ticket launch context",
            {},
        )
    created_conversation_id = conversation_start.new_conversation_id()
    runs_under = _what_this_message_runs_under(body)
    admission = video_send_admission(
        request,
        message_files,
        body.content,
        body.sender_message_id,
        UploadDestination(kind="chief", id="chief", expected_conversation_id=body.conversation_id),
        CHIEF_PRINCIPAL,
        ctx.principal,
    )
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clk,
        ctx,
        CHIEF_PRINCIPAL,
        admission
        or partial(
            conversation_message_content,
            message_files,
            sent=body.content,
            sender_message_id=body.sender_message_id,
        ),
        admission=admission,
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=runs_under,
        mode=body.mode,
        owner_read_through_sequence=body.owner_read_through_sequence,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
    )
    return _delivered_message_json(delivered)


@router.post("/chief/conversation/reset")
async def reset_chief_conversation(
    conn: DbConn, ctx: Ctx, conversations: Conversations
) -> JsonDict:
    """Cut the Chief loose from its conversation. This is what New does."""
    require_above(conn, ctx.principal, authority.owner_only("chief conversation"))
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
    request: Request,
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
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    created_conversation_id = conversation_start.new_conversation_id()
    admission = video_send_admission(
        request,
        message_files,
        body.content,
        body.sender_message_id,
        UploadDestination(
            kind="ticket", id=ticket_id, expected_conversation_id=body.conversation_id
        ),
        Principal(PrincipalKind.ticket, ticket_id),
        ctx.principal,
    )
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clk,
        ctx,
        Principal(PrincipalKind.ticket, ticket_id),
        admission
        or partial(
            conversation_message_content,
            message_files,
            sent=body.content,
            sender_message_id=body.sender_message_id,
        ),
        admission=admission,
        conversation_id=body.conversation_id,
        created_conversation_id=created_conversation_id,
        runs_under=_what_this_message_runs_under(body),
        mode=body.mode,
        owner_read_through_sequence=body.owner_read_through_sequence,
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
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    now = clk.now_unix()
    await conversation_start.reset_ticket_conversation(conversations, conn, ticket_id, now=now)
    return tickets_views.ticket_json(tickets_data.read_ticket(conn, ticket_id), now)


@router.post("/tickets/{ticket_id}/complete/{field}")
async def complete_user_owned_gate(
    ticket_id: str,
    field: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    """The user does a user-owned Stage's work themselves, and the Stage advances.

    This is not a field edit and does not live on the edit path. It fills a blank the
    Ticket is gated on and moves the Ticket forward, which is a consequence only an
    approval otherwise has.
    """
    body = GateCompletionBody(body=body_str(raw, "body"))
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    _ticket, worker_type_definition = _ticket_and_worker_type_definition(conn, ticket_id)
    _validate_field(worker_type_definition, field)
    await reject_while_the_conversation_is_running(conn, conversations, ticket_id)
    now = clk.now_unix()
    ticket = tickets_data.complete_user_owned_gate(
        conn,
        ticket_id,
        field=field,
        new_body=body["body"],
        principal=ctx.principal,
        now=now,
    )
    return tickets_views.ticket_json(ticket, now)


@router.post("/tickets/{ticket_id}/request-help")
async def request_help(
    ticket_id: str,
    body: JsonDict,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
    conversations: Conversations,
) -> JsonDict:
    require_self(conn, ctx.principal, authority.ticket(ticket_id))
    if set(body) - {"message", "recipient"}:
        raise PlannerError(ErrorCode.validation, "unknown help request field", {})
    message = body_str(body, "message")
    if not message.strip():
        raise PlannerError(ErrorCode.validation, "help message must not be empty", {})
    ticket = tickets_data.read_ticket(conn, ticket_id)
    named = _parse_principal(body.get("recipient"), "recipient")
    if named is not None:
        # The same question Send Message asks. Without it this is a second, open door
        # into any conversation in Panels. The default below is not asked, because the
        # ceiling holder is the principal Panels itself addressed this Ticket to.
        message_delivery_service.require_reach(conn, ctx.principal, named)
    recipient = named or ticket.ceiling_holder
    delivered = await message_delivery_service.send_message(
        conversations, conn, clk, ctx, recipient, message
    )
    fate = (
        {"fate": "recorded"}
        if isinstance(delivered.fate, MessageRecordedToOwner)
        else delivery_fate_json(delivered.fate)
    )
    return {
        "target": {"kind": recipient.kind.value, "id": recipient.id},
        "conversation_id": delivered.conversation_id,
        **fate,
    }


@router.get("/tickets/{ticket_id}/copy-text", response_class=PlainTextResponse)
async def ticket_copy_text(ticket_id: str, conn: DbConn) -> str:
    return tickets_views.copy_text(conn, ticket_id)


async def add_conversation_row_signals(
    conn: sqlite3.Connection,
    board: JsonDict,
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
) -> JsonDict:
    """Attach the shared owner-attention and agent-state projection to board rows."""
    cards = [card for column in board["columns"] for card in column["cards"]]
    children = [ticket for item in board["managers"] for ticket in item["tickets"]]
    await add_work_attention(
        conn,
        conversation_system,
        conversation_record,
        tickets=(*cards, *children),
        managers=board["managers"],
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
    return await add_conversation_row_signals(
        conn,
        tickets_views.board_view(conn),
        conversations,
        conversation_record,
    )


@router.get("/review")
async def review(
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    response = tickets_views.review_view(conn)
    response["running_worker_count"] = await read_running_conversation_count(
        conn, conversations, conversation_record
    )
    return response


def _record_precondition(body: dict[str, Any]) -> str | None:
    value = body.get("expected_record_sha256")
    if value is not None and (not isinstance(value, str) or len(value) != 64):
        raise PlannerError(ErrorCode.validation, "expected_record_sha256 must be a SHA256 string")
    return value


@router.post("/tickets/{ticket_id}/backlog")
async def backlog_ticket(
    ticket_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    tickets_data.set_ticket_backlog(
        conn,
        ticket_id,
        is_backlogged=True,
        principal=ctx.principal,
        now=clk.now_unix(),
        expected_record_sha256=_record_precondition(body),
    )
    return _ticket_detail(conn, ticket_id, clk.now_unix())


@router.post("/tickets/{ticket_id}/resume")
async def resume_ticket(
    ticket_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    tickets_data.set_ticket_backlog(
        conn,
        ticket_id,
        is_backlogged=False,
        principal=ctx.principal,
        now=clk.now_unix(),
        expected_record_sha256=_record_precondition(body),
    )
    return _ticket_detail(conn, ticket_id, clk.now_unix())
