"""Atomically retain a Review decision outcome with its canonical Ticket transition.

A receipt records only the Ticket transition, not delivery into a Worker conversation.
The full submitted command, including omitted versus explicit null fields, is its identity.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.tickets import data
from planner.tickets.contracts import (
    DecisionCommand,
    DecisionError,
    DecisionHolder,
    DecisionOperationResponse,
    DecisionReceipt,
    DecisionTransition,
    Ticket,
)
from planner.tickets.integrity import TicketIntegrityError

# Only these canonical decision refusals become terminal receipts. Operational,
# authority, integrity, and unknown failures propagate and roll back the entire write.
_BUSINESS_REFUSALS = frozenset({
    ErrorCode.validation, ErrorCode.scope_missing, ErrorCode.scope_invalid,
    ErrorCode.not_found, ErrorCode.already_running,
})


def _validation(message: str) -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


def _identity(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise _validation(f"{name} must be nonempty text without surrounding whitespace")


def _holder(value: object) -> Principal | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"kind", "id"}:
        raise _validation("next_holder must name kind and id")
    kind, identifier = value["kind"], value["id"]
    if kind not in ("owner", "chief", "manager") or not isinstance(identifier, str):
        raise _validation("next_holder must name owner, chief, or manager with a text id")
    try:
        return Principal(PrincipalKind(kind), identifier)
    except ValueError as exc:
        raise _validation(str(exc)) from exc


def parse_command(raw: dict[str, Any]) -> DecisionCommand:
    """Validate transport shapes without normalizing away command identity."""
    decision = raw.get("decision")
    if decision not in ("approve", "send_back"):
        raise _validation("decision must be approve or send_back")
    allowed = {"decision", "field", "expected_proposal_revision"} | (
        {"edited_body", "next_ceiling", "next_holder"} if decision == "approve" else {"message"}
    )
    if set(raw) - allowed:
        raise _validation("unknown decision command fields")
    field = raw.get("field")
    if not isinstance(field, str):
        raise _validation("field must be text")
    _identity(field, "field")
    revision = raw.get("expected_proposal_revision")
    if type(revision) is not int or revision <= 0:
        raise _validation("expected_proposal_revision must be a positive integer")
    for name in ("edited_body", "next_ceiling", "message"):
        if name in raw and raw[name] is not None and not isinstance(raw[name], str):
            raise _validation(f"{name} must be text or null")
    if "next_holder" in raw:
        _holder(raw["next_holder"])
    return cast(DecisionCommand, dict(raw))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_ticket(conn: sqlite3.Connection, ticket_id: str) -> None:
    if conn.execute("SELECT 1 FROM tickets WHERE id=?", (ticket_id,)).fetchone() is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})


def _saved(
    conn: sqlite3.Connection, ticket_id: str, operation_id: str, actor: Principal,
) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM ticket_decision_operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is not None and (row["actor_kind"], row["actor_id"]) != (actor.kind.value, actor.id):
        raise PlannerError(
            ErrorCode.agent_forbidden, "this decision operation belongs to another actor",
        )
    if row is not None and row["ticket_id"] != ticket_id:
        raise _validation("decision operation identity was reused for another Ticket")
    return cast(sqlite3.Row | None, row)


def lookup(
    conn: sqlite3.Connection, ticket_id: str, operation_id: str, *, actor: Principal,
) -> DecisionOperationResponse:
    _identity(operation_id, "operation_id")
    # A receipt is private to its actor even when another actor can read the Ticket.
    _require_ticket(conn, ticket_id)
    authority.require_above(conn, actor, authority.ticket(ticket_id))
    row = _saved(conn, ticket_id, operation_id, actor)
    return DecisionOperationResponse(
        receipt_contract=1,
        receipt=None if row is None else cast(DecisionReceipt, json.loads(row["receipt_json"])),
    )


def _apply(
    conn: sqlite3.Connection, ticket_id: str, command: DecisionCommand,
    *, current_ticket: Ticket, actor: Principal, now: int,
) -> DecisionTransition:
    field = command["field"]
    if command["decision"] == "approve":
        holder = _holder(command.get("next_holder"))
        if holder is None:
            raise PlannerError(
                ErrorCode.scope_missing, "approval requires next_holder",
                {"missing": ["next_holder"]},
            )
        ticket = data.accept_proposal(
            conn, ticket_id, field=field, principal=actor, now=now,
            edited_body=command.get("edited_body"), next_ceiling=command.get("next_ceiling"),
            next_holder=holder, expected_proposal_revision=command["expected_proposal_revision"],
        )
    else:
        proposal = current_ticket.pending_proposal
        if proposal is None or proposal.field != field:
            raise _validation("proposal field is not the current gate")
        ticket = data.reject_proposal(
            conn, ticket_id, principal=actor, now=now, message=command.get("message"),
            expected_proposal_revision=command["expected_proposal_revision"],
        )
    return DecisionTransition(
        stage=ticket.stage, ceiling=ticket.ceiling,
        ceiling_holder=cast(DecisionHolder, {
            "kind": ticket.ceiling_holder.kind.value, "id": ticket.ceiling_holder.id,
        }),
        ticket_status=ticket.ticket_status.value,
        pending_proposal_revision=ticket.pending_proposal_revision,
    )


def apply(
    conn: sqlite3.Connection, ticket_id: str, operation_id: str, raw: dict[str, Any],
    *, actor: Principal, now: int,
) -> DecisionOperationResponse:
    _identity(operation_id, "operation_id")
    command = parse_command(raw)
    command_json = _json(command)
    with data._txn(conn):
        _require_ticket(conn, ticket_id)
        authority.require_above(conn, actor, authority.ticket(ticket_id))
        row = _saved(conn, ticket_id, operation_id, actor)
        if row is not None:
            if row["command_json"] != command_json:
                raise _validation("decision operation identity was reused with different content")
            return DecisionOperationResponse(
                receipt_contract=1, receipt=cast(DecisionReceipt, json.loads(row["receipt_json"])),
            )
        # Structural stored-state failures are not terminal decision refusals.
        # This remains after duplicate recovery, which needs no current workflow.
        current_ticket, _definition = data._load_ticket_and_worker_type_definition_for_write(
            conn, ticket_id,
        )
        error: DecisionError | None = None
        transition: DecisionTransition | None = None
        try:
            # Roll back partial canonical work before retaining an expected refusal.
            with data._txn(conn):
                transition = _apply(
                    conn, ticket_id, command, current_ticket=current_ticket, actor=actor, now=now,
                )
        except PlannerError as exc:
            if isinstance(exc, TicketIntegrityError) or exc.code not in _BUSINESS_REFUSALS:
                raise
            error = DecisionError(code=exc.code.value, message=exc.message, detail=exc.detail)
        receipt = DecisionReceipt(
            operation_id=operation_id, ticket_id=ticket_id, decision=command["decision"],
            field=command["field"],
            expected_proposal_revision=command["expected_proposal_revision"],
            outcome="applied" if error is None else "refused", transition=transition, error=error,
            created_at=now,
        )
        conn.execute(
            "INSERT INTO ticket_decision_operations "
            "(ticket_id,operation_id,actor_kind,actor_id,command_json,receipt_json,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (ticket_id, operation_id, actor.kind.value, actor.id, command_json,
             _json(receipt), now),
        )
        return DecisionOperationResponse(receipt_contract=1, receipt=receipt)
