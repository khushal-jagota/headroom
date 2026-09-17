"""The item-scoped application service for Sprint Item supervisors."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryInjected,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
)
from planner.core.authctx import RequestContext, require_sprint_item_supervisor_ticket_write
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.files.logic.paths import sprint_item_files_root
from planner.message_delivery import service as message_delivery_service
from planner.runtime import conversation_start, worker_step_readiness
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import StageOwnershipMode, Ticket, TicketStatus
from planner.tickets.logic import machine
from planner.worker_types.configuration import configured_worker_type_registry

MAXIMUM_HISTORY_EVENTS = 100
SUPERVISOR_ARTIFACTS_DIRECTORY = "artifacts"
# A worker step gets this long to prove it is alive before anyone may restart it.
WORKER_STEP_RESTART_FLOOR_SECONDS = 300


def require_current_child(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
) -> Ticket:
    """Return the child after the shared server-side authority check."""
    require_sprint_item_supervisor_ticket_write(conn, ctx, sprint_item_id, ticket_id)
    return tickets_data.read_ticket(conn, ticket_id)


def ticket_context(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    now: int,
    triggering_message_sequence: int | None,
) -> dict[str, object]:
    with _coherent_read(conn):
        ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
        triggering_message = None
        if triggering_message_sequence is not None and ticket.conversation_id is not None:
            row = conn.execute(
                "SELECT sequence, kind, payload, created_at FROM conversation_events "
                "WHERE conversation_id = ? AND kind = 'agent_message' AND sequence = ?",
                (ticket.conversation_id, triggering_message_sequence),
            ).fetchone()
            if row is not None:
                triggering_message = _event_json(row)
        if triggering_message_sequence is not None and triggering_message is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the triggering Worker message is not in the current conversation",
                {
                    "ticket_id": ticket_id,
                    "conversation_id": ticket.conversation_id,
                    "sequence": triggering_message_sequence,
                },
            )
        day_ids = [
            str(row["day_id"])
            for row in conn.execute(
                "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id",
                (ticket_id,),
            ).fetchall()
        ]
        return {
            "sprint_item_id": sprint_item_id,
            "ticket": tickets_views.ticket_detail(conn, ticket_id, now),
            "day_ids": day_ids,
            "conversation_id": ticket.conversation_id,
            "triggering_worker_message": triggering_message,
        }


def conversation_history(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    limit: int,
    before_sequence: int | None,
) -> dict[str, object]:
    if limit < 1 or limit > MAXIMUM_HISTORY_EVENTS:
        raise PlannerError(
            ErrorCode.validation,
            f"limit must be between 1 and {MAXIMUM_HISTORY_EVENTS}",
            {"limit": limit},
        )
    with _coherent_read(conn):
        ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
        if ticket.conversation_id is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the ticket has no current Worker conversation",
                {"ticket_id": ticket_id},
            )
        params: list[object] = [ticket.conversation_id]
        before_clause = ""
        if before_sequence is not None:
            before_clause = " AND sequence < ?"
            params.append(before_sequence)
        params.append(limit)
        rows = conn.execute(
            "SELECT * FROM ("
            "SELECT sequence, kind, payload, created_at FROM conversation_events "
            "WHERE conversation_id = ?"
            + before_clause
            + " ORDER BY sequence DESC LIMIT ?) ORDER BY sequence",
            tuple(params),
        ).fetchall()
        events = [_event_json(row) for row in rows]
        return {
            "sprint_item_id": sprint_item_id,
            "ticket_id": ticket_id,
            "conversation_id": ticket.conversation_id,
            "events": events,
            "has_more": bool(events and int(str(events[0]["sequence"])) > 1),
        }


@contextmanager
def _coherent_read(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold one read result against child moves and conversation resets.

    A deferred ``BEGIN`` fixes one WAL snapshot at the scope check and does not reserve
    the writer. ``ROLLBACK`` closes an owned read on every exit without emitting the
    process change signal. Existing transaction owners keep ownership and supply their
    own coherent boundary.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")


def require_restartable_child(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    now: int,
) -> Ticket:
    """Return the child, once every reason not to restart it has been ruled out.

    Nothing is asked about whether the worker is alive, because nothing can answer it.
    ``is_running`` reads in-process bookkeeping that nothing clears when a backend dies,
    so a dead worker looks like a running one forever, and a guard on it would refuse the
    exact state a restart is for. The supervisor decides that its worker is dead, from
    the conversation history it can already read, and the checks here bound what that
    decision can reach.
    """
    ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)
    ownership_mode = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
    )
    if ownership_mode is not StageOwnershipMode.worker:
        # A Paired Stage rests where it departs, so no code can tell a dead paired worker
        # from a discussion waiting on the user, and a restart there would kill a
        # conversation the user is in.
        raise PlannerError(
            ErrorCode.validation,
            "only a Worker-owned Stage has a worker step to restart",
            {"ticket_id": ticket_id, "stage": ticket.stage},
        )
    if ticket.ticket_status is TicketStatus.agent:
        age = now - ticket.ticket_status_changed_at
        if age < WORKER_STEP_RESTART_FLOOR_SECONDS:
            # The one bound on a restart loop: each restart resets this clock, so a
            # supervisor that keeps restarting has to wait out the floor every time.
            raise PlannerError(
                ErrorCode.validation,
                "this worker step is too young to restart",
                {
                    "ticket_id": ticket_id,
                    "age_seconds": age,
                    "floor_seconds": WORKER_STEP_RESTART_FLOOR_SECONDS,
                },
            )
        return ticket
    if ticket.ticket_status is TicketStatus.errored:
        return ticket
    if ticket.conversation_id is None:
        # A restart whose start was refused lands here. Restarting again is how a
        # supervisor corrects the launch configuration it named the first time.
        return ticket
    raise PlannerError(
        ErrorCode.validation,
        "the Ticket has no worker step out to restart",
        {"ticket_id": ticket_id, "ticket_status": ticket.ticket_status.value},
    )


async def restart_worker(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    write_employee_configuration: Callable[[sqlite3.Connection], None] | None,
    start_worker_step: Callable[[], Awaitable[bool]],
    planning_day_id: str,
    now: int,
) -> dict[str, object]:
    """Kill this child Ticket's dead conversation, give the claim back, and start again.

    The claim is the status, so a reset on its own would leave the Ticket at ``agent``
    with no conversation and nothing that ever picks it up. Both halves happen here, and
    the supervisor sees one action.

    ``write_employee_configuration`` is already validated when it arrives: the caller
    resolved it against the backend registry and catalog before anything was killed, so a
    bad argument costs the Ticket nothing. It runs in the same transaction as the claim,
    after it, because the launch values unfreeze only once the Ticket is out of ``agent``.
    """
    ticket = require_restartable_child(conn, ctx, sprint_item_id, ticket_id, now=now)
    killed_conversation_id = ticket.conversation_id
    killed_conversation_looked_running = (
        await conversations.is_running(killed_conversation_id)
        if killed_conversation_id is not None
        else False
    )
    if killed_conversation_id is not None:
        await conversation_start.reset_ticket_conversation(conversations, conn, ticket_id, now=now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if ticket.ticket_status is TicketStatus.agent:
            given_back = tickets_data.release_worker_step_claim(
                conn,
                ticket_id,
                expected_status=ticket.ticket_status,
                expected_status_revision=ticket.ticket_status_revision,
                now=now,
            )
            if not given_back:
                raise PlannerError(
                    ErrorCode.already_running,
                    "the Ticket moved while it was being restarted",
                    {"ticket_id": ticket_id},
                )
        elif ticket.ticket_status is TicketStatus.errored:
            tickets_data.clear_ticket_error_for_restart(conn, ticket_id, now=now)
        if write_employee_configuration is not None:
            write_employee_configuration(conn)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    started = await start_worker_step()
    restarted = tickets_data.read_ticket(conn, ticket_id)
    return {
        "sprint_item_id": sprint_item_id,
        "ticket_id": ticket_id,
        "killed_conversation_id": killed_conversation_id,
        "killed_conversation_looked_running": killed_conversation_looked_running,
        "employee_configuration": {
            "employee_backend": restarted.employee_backend,
            "employee_launch_model": restarted.employee_launch_model,
            "employee_launch_reasoning_effort": restarted.employee_launch_reasoning_effort,
        },
        "ticket_status": restarted.ticket_status.value,
        "conversation_id": restarted.conversation_id,
        "started": started,
        "not_started_because": (
            None
            if started
            else worker_step_readiness.worker_step_blocker(
                conn,
                restarted,
                planning_day_id=planning_day_id,
                worker_type_definition=configured_worker_type_registry().require(
                    restarted.worker_type
                ),
            )
        ),
    }


async def message_current_worker(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    message: str,
    clock: Clock,
) -> dict[str, object]:
    if not message.strip():
        raise PlannerError(ErrorCode.validation, "Worker message must be non-empty", {})
    require_current_child(conn, ctx, sprint_item_id, ticket_id)
    item = sprints_data.read_item(conn, sprint_item_id).item
    delivered = await message_delivery_service.send_message(
        conversations,
        conn,
        clock,
        ctx,
        Principal(PrincipalKind.ticket, ticket_id),
        message.strip(),
        required_sprint_item_id=sprint_item_id,
    )
    if isinstance(delivered.fate, PromptDeliveryRefused):
        raise PlannerError(
            ErrorCode.gateway_offline,
            "the Worker message could not be delivered",
            {
                "ticket_id": ticket_id,
                "refusal_reason": delivered.fate.refusal_reason.value,
            },
        )
    return {
        "sprint_item_id": sprint_item_id,
        "ticket_id": ticket_id,
        "conversation_id": delivered.conversation_id,
        "sender": item.supervisor_agent_key,
        "fate": (
            "queued"
            if isinstance(delivered.fate, PromptDeliveryQueued)
            else "injected"
            if isinstance(delivered.fate, PromptDeliveryInjected)
            else "started"
        ),
    }


def list_artifacts(
    conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str, db_path: str
) -> dict[str, object]:
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=False)
    paths = (
        []
        if root is None
        else sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    )
    return {"sprint_item_id": sprint_item_id, "artifacts": paths}


def list_artifact_details(
    conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str, db_path: str
) -> list[dict[str, object]]:
    """Return read-only file facts for the Sprint Item workspace."""
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=False)
    if root is None:
        return []
    return [
        {
            "path": f"{SUPERVISOR_ARTIFACTS_DIRECTORY}/{path.relative_to(root).as_posix()}",
            "modified_at": path.stat().st_mtime,
        }
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]


def write_artifact(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    db_path: str,
    relative_path: str,
    content: str,
) -> dict[str, object]:
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=True)
    assert root is not None
    target = _safe_artifact_target(root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(root, target)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "sprint_item_id": sprint_item_id,
        "path": f"{SUPERVISOR_ARTIFACTS_DIRECTORY}/{relative_path}",
        "url": (
            f"/files/sprint-items/{sprint_item_id}/{SUPERVISOR_ARTIFACTS_DIRECTORY}/{relative_path}"
        ),
    }


def delete_artifact(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    db_path: str,
    relative_path: str,
) -> dict[str, object]:
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=False)
    if root is None:
        raise PlannerError(ErrorCode.not_found, "Sprint Item artifact not found", {})
    target = _safe_artifact_target(root, relative_path)
    _reject_symlink_ancestors(root, target)
    if not target.is_file() or target.is_symlink():
        raise PlannerError(ErrorCode.not_found, "Sprint Item artifact not found", {})
    target.unlink()
    return {"ok": True, "sprint_item_id": sprint_item_id, "path": relative_path}


def _event_json(row: sqlite3.Row) -> dict[str, object]:
    return {
        "sequence": int(row["sequence"]),
        "kind": str(row["kind"]),
        "payload": json.loads(str(row["payload"])),
        "created_at": int(row["created_at"]),
    }


def _require_item(conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str) -> None:
    from planner.core.authctx import require_sprint_item_supervisor_read

    require_sprint_item_supervisor_read(conn, ctx, sprint_item_id)


def _artifact_root(db_path: str, sprint_item_id: str, *, create: bool) -> Path | None:
    files_root = sprint_item_files_root(db_path)
    if files_root.is_symlink():
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
    item_root = files_root / sprint_item_id
    if create:
        item_root.mkdir(parents=True, exist_ok=True)
    elif not item_root.is_dir() or item_root.is_symlink():
        return None
    root = item_root / SUPERVISOR_ARTIFACTS_DIRECTORY
    if create:
        if item_root.is_symlink() or root.is_symlink():
            raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
        root.mkdir(exist_ok=True)
    elif not root.is_dir() or root.is_symlink():
        return None
    return root.resolve()


def _safe_artifact_target(root: Path, relative_path: str) -> Path:
    parsed = PurePosixPath(relative_path)
    if (
        not relative_path
        or parsed.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
    target = root.joinpath(*parsed.parts)
    try:
        target.parent.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {}) from exc
    return target


def _reject_symlink_ancestors(root: Path, target: Path) -> None:
    current = target.parent
    while current != root:
        if current.is_symlink():
            raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
        current = current.parent
