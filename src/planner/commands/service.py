"""Command policies and their single rank admission boundary.

Callers name the operation and its object. Only this module selects the rank relation.
Admission stays inside the caller's existing transaction and validation sequence.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Literal

from planner.core import authority
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError

Relation = Literal["above", "above_or_self", "self", "in_chain"]
Kind = Literal["ticket", "manager", "goal", "owner_only"]
_POLICIES: dict[str, tuple[Relation, Kind]] = {
    "conversation._read": ("above_or_self", "manager"),
    "conversation._read.goal": ("above_or_self", "goal"),
    "conversation.read_scoped_backends": ("above_or_self", "ticket"),
    "credentials.delete_credential": ("above", "owner_only"),
    "feedback.create_feedback": ("above", "owner_only"),
    "feedback.dismiss_feedback": ("above", "owner_only"),
    "feedback.reopen_feedback": ("above", "owner_only"),
    "feedback.write_use_feedback": ("above_or_self", "ticket"),
    "files._read": ("above_or_self", "manager"),
    "files._require_manager": ("above_or_self", "manager"),
    "files.get_manager_file": ("above_or_self", "manager"),
    "files.require_goal": ("above_or_self", "goal"),
    "goals.create_goal": ("above", "owner_only"),
    "goals.reset": ("above", "goal"),
    "goals.send": ("above", "goal"),
    "goals.start_values": ("above_or_self", "goal"),
    "goals.update_goal": ("above_or_self", "goal"),
    "managers.capture": ("above_or_self", "manager"),
    "managers.delete_manager": ("above", "manager"),
    "managers.get_item_conversation_start_values": ("above_or_self", "manager"),
    "managers.get_manager_launch_configuration": ("above_or_self", "manager"),
    "managers.get_manager_workspace": ("above_or_self", "manager"),
    "managers.get_manager_workspace_content": ("above_or_self", "manager"),
    "managers.patch_item": ("above_or_self", "manager"),
    "managers.put_manager_launch_configuration": ("above", "manager"),
    "managers.reset_item_conversation": ("above", "manager"),
    "managers.send_to_item_conversation": ("above", "manager"),
    "managers.update_manager_launch_configuration": ("above", "manager"),
    "measure_sources.require_owner_or_chief": ("above", "owner_only"),
    "measures._require_author": ("above", "owner_only"),
    "membership._blocker_rule": ("above_or_self", "ticket"),
    "membership._manager_ticket_rule": ("above", "ticket"),
    "projects.create_project": ("above", "owner_only"),
    "projects.update_project": ("above", "owner_only"),
    "scheduled_tickets.create_schedule": ("above", "owner_only"),
    "scheduled_tickets.patch_schedule": ("above", "owner_only"),
    "scheduled_tickets.update_schedule": ("above", "owner_only"),
    "tickets.accept_proposal": ("above", "ticket"),
    "tickets.complete_user_owned_gate": ("above", "ticket"),
    "tickets.delete_ticket": ("above", "ticket"),
    "tickets.put_ticket_employee_configuration": ("above", "ticket"),
    "tickets.reject_proposal": ("above", "ticket"),
    "tickets.request_help": ("self", "ticket"),
    "tickets.require_reject": ("above", "ticket"),
    "tickets.require_restartable": ("above", "ticket"),
    "tickets.reset_chief_conversation": ("above", "owner_only"),
    "tickets.reset_ticket_conversation": ("above", "ticket"),
    "tickets.restart_ticket_worker": ("above", "ticket"),
    "tickets.send_to_chief_conversation": ("above", "owner_only"),
    "tickets.send_to_ticket_conversation": ("above", "ticket"),
    "tickets.set_ticket_backlog": ("above", "ticket"),
    "tickets.write_patch_ticket": ("above_or_self", "ticket"),
    "worker_settings.apply_skills": ("above", "owner_only"),
    "worker_settings.patch_chief_skill": ("above", "owner_only"),
    "worker_settings.patch_skill": ("above", "owner_only"),
    "worker_settings.patch_worker_skill": ("above", "owner_only"),
    "worker_settings.post_worker_type": ("above", "owner_only"),
    "worker_settings.put_chief_launch_defaults": ("above", "owner_only"),
    "worker_settings.put_chief_skill": ("above", "owner_only"),
    "worker_settings.put_worker_launch_defaults": ("above", "owner_only"),
    "worker_settings.put_worker_skill": ("above", "owner_only"),
}


def admit(
    conn: sqlite3.Connection, principal: Principal, operation: str, identifier: str, *fields: str
) -> None:
    """Admit one operation through its declared command policy."""
    relation, kind = _POLICIES[operation]
    target = authority.Target(authority.TargetKind(kind), identifier, frozenset(fields))
    _admit_target(conn, principal, relation, target)
    if operation == "membership._manager_ticket_rule":
        authority.refuse_manager_re_parenting(principal, identifier)


def _admit_target(
    conn: sqlite3.Connection, principal: Principal, relation: Relation, target: authority.Target
) -> None:
    checks = {
        "above": authority.require_above,
        "above_or_self": authority.require_above_or_self,
        "self": authority.require_self,
        "in_chain": authority.require_in_chain,
    }
    checks[relation](conn, principal, target)


def admit_ticket_creation(
    conn: sqlite3.Connection,
    principal: Principal,
    *,
    ticket_id: str,
    manager_id: str | None,
    stated_ceiling: object,
    stated_holder: object,
) -> None:
    """Check only the authority-bearing values of a new Ticket."""
    if manager_id is not None:
        _admit_target(conn, principal, "in_chain", authority.manager(manager_id))
    if stated_ceiling is not None or stated_holder is not None:
        authority.require_above_a_ticket_being_created(
            conn, principal, ticket_id=ticket_id, parent_manager_id=manager_id
        )


def admit_ticket_edit(
    conn: sqlite3.Connection, principal: Principal, ticket_id: str, edit: Mapping[str, object]
) -> None:
    """Reserve canonical control fields and assignment for the existing higher rank."""
    from planner.tickets.logic import admission

    relation: Relation = (
        "above" if set(edit) & admission.TICKET_FIELDS_ONLY_FROM_ABOVE else "above_or_self"
    )
    _admit_target(conn, principal, relation, authority.ticket(ticket_id))
    if admission.TICKET_FIELD_THAT_MOVES_THE_PARENT in edit or "manager" in edit:
        authority.refuse_manager_re_parenting(principal, ticket_id)


def admit_message(conn: sqlite3.Connection, caller: Principal, recipient: Principal) -> None:
    """Sending down the chain is acting on the recipient. Sending up is only speaking.

    Two principals in one chain can talk, in both directions. Standing above the
    recipient is acting on it — starting a turn in a conversation that belongs to
    something below you — and that is the rule's own question. Standing below it is not:
    a Worker answering the Outcome that asked it something is speaking, and refusing that
    would make the reply every addressed prompt requires impossible to send.

    Existing ordinary Managers can also speak to one another. This
    message-only permission grants no authority over either supervisor or its Tickets.
    Other strangers are neither, and they refuse.

    Asked by the two routes that take a recipient from their caller, and it lives here so
    they ask the same question rather than two. It is not asked inside ``send_message``:
    the other callers name a recipient the route has already guarded, and a Ticket asking
    its own ceiling holder for help names a principal Panels addressed it to, which is not
    the caller reaching anywhere.
    """
    from planner.managers import data as managers_data
    from planner.message_delivery.logic.permissions import permits_manager_message

    if permits_manager_message(caller, recipient):
        managers_data.read_manager(conn, caller.id)
        managers_data.read_manager(conn, recipient.id)
        return
    target = _as_target(recipient)
    if target is None:
        return
    if authority.is_above(conn, caller, target):
        return
    caller_target = _as_target(caller)
    if caller_target is not None and authority.is_above(conn, recipient, caller_target):
        return
    _admit_target(conn, caller, "above", target)


def admit_source_assignment(
    conn: sqlite3.Connection, principal: Principal, responsible: Principal
) -> None:
    from planner.tickets import data as tickets_data

    if responsible.kind is PrincipalKind.goal:
        _admit_target(conn, principal, "above_or_self", authority.goal(responsible.id))
        return
    if responsible.kind is PrincipalKind.manager:
        _admit_target(conn, principal, "in_chain", authority.manager(responsible.id))
        return
    if principal.kind is PrincipalKind.ticket:
        ticket = tickets_data.read_ticket(conn, principal.id)
        if ticket.manager_kind == "chief":
            return
        raise PlannerError(ErrorCode.agent_forbidden, "source belongs to another assignment")
    admit(conn, principal, "measure_sources.require_owner_or_chief", "measure_sources")


def _as_target(principal: Principal) -> authority.Target | None:
    """The thing this principal is, when something can stand above it.

    Khushal and the Chief are above everything, so nothing is above them and they are no
    target. A message addressed to either asks no authority question.
    """
    if principal.kind is PrincipalKind.goal:
        return authority.goal(principal.id)
    if principal.kind is PrincipalKind.ticket:
        return authority.ticket(principal.id)
    if principal.kind is PrincipalKind.manager:
        return authority.manager(principal.id)
    return None
