"""One projection for what needs the owner and what each employee is doing."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable
from typing import TypedDict

from planner.conversation.contracts import ConversationSystem
from planner.conversation.storage import ConversationAttentionFacts, ConversationStore
from planner.core import ticket_blocks
from planner.core.contracts import OWNER_PRINCIPAL, JsonDict, Principal
from planner.tickets import derivation
from planner.tickets.contracts import StageOwnershipMode, TicketStatus
from planner.tickets.derivation import AgentState, TicketFacts
from planner.tickets.logic import machine
from planner.worker_types.configuration import configured_worker_type_registry


class WorkAttention(TypedDict):
    awaiting_reply: bool
    awaiting_approval: bool
    assigned: bool
    agent_state: str


def _empty_attention() -> WorkAttention:
    return {
        "awaiting_reply": False,
        "awaiting_approval": False,
        "assigned": False,
        "agent_state": AgentState.idle.value,
    }


async def _conversation_attention(
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
    conversation_ids: set[str],
) -> dict[str, tuple[bool, bool, bool]]:
    durable = await conversation_record.attention_facts(conversation_ids)

    async def one(conversation_id: str) -> tuple[str, bool, bool, bool]:
        running, permission, question = await asyncio.gather(
            conversation_system.is_running(conversation_id),
            conversation_system.has_pending_permission_ask(conversation_id),
            conversation_system.has_pending_user_input(conversation_id),
        )
        fact = durable.get(conversation_id, ConversationAttentionFacts(False, False))
        return (
            conversation_id,
            fact.unread_message_to_owner or permission or question,
            running,
            fact.last_turn_failed,
        )

    return {
        conversation_id: (awaiting_reply, running, last_turn_failed)
        for conversation_id, awaiting_reply, running, last_turn_failed in await asyncio.gather(
            *(one(conversation_id) for conversation_id in conversation_ids)
        )
    }


def _ticket_rows(conn: sqlite3.Connection, ticket_ids: set[str]) -> dict[str, sqlite3.Row]:
    if not ticket_ids:
        return {}
    placeholders = ",".join("?" for _ in ticket_ids)
    rows = conn.execute(
        "SELECT id, stage, worker_type, worker_step_claim, pending_proposal, ceiling_holder, "
        "conversation_id "
        f"FROM tickets WHERE id IN ({placeholders})",
        tuple(sorted(ticket_ids)),
    ).fetchall()
    return {str(row["id"]): row for row in rows}


def _ticket_attention(
    row: sqlite3.Row,
    conversation: tuple[bool, bool, bool] | None,
    *,
    facts: TicketFacts,
    approval_holder: Principal,
) -> WorkAttention:
    holder = json.loads(str(row["ceiling_holder"]))
    owner_holds_ceiling = holder == {
        "kind": OWNER_PRINCIPAL.kind.value,
        "id": OWNER_PRINCIPAL.id,
    }
    awaiting_approval = facts.ticket_status is TicketStatus.awaiting_approval and holder == {
        "kind": approval_holder.kind.value,
        "id": approval_holder.id,
    }
    awaiting_reply, running, last_turn_failed = conversation or (False, False, False)
    assigned = ticket_assignment_from_values(
        stage=str(row["stage"]),
        worker_type=str(row["worker_type"]),
        owner_holds_ceiling=owner_holds_ceiling,
    )
    agent_state = derivation.agent_state(
        facts.ticket_status,
        turn_is_running=running,
        last_turn_failed=last_turn_failed,
    )
    return {
        "awaiting_reply": awaiting_reply,
        "awaiting_approval": awaiting_approval,
        "assigned": assigned,
        "agent_state": agent_state.value,
    }


def ticket_is_assigned(
    stage: str,
    ownership: StageOwnershipMode | None,
    owner_holds_ceiling: bool,
) -> bool:
    """Whether the current Ticket stage is Khushal's work."""
    return ownership is StageOwnershipMode.user or (
        stage == "needs_kickoff" and owner_holds_ceiling
    )


def ticket_assignment_from_values(
    *,
    stage: str,
    worker_type: str,
    owner_holds_ceiling: bool,
) -> bool:
    """Derive assignment from the Worker type's ownership declaration."""
    definition = configured_worker_type_registry().require(worker_type)
    ownership = machine.stage_ownership_mode(
        stage,
        worker_type_definition=definition,
    )
    return ticket_is_assigned(stage, ownership, owner_holds_ceiling)


def _roll_up(children: Iterable[WorkAttention]) -> WorkAttention:
    rows = tuple(children)
    states = {row["agent_state"] for row in rows}
    return {
        "awaiting_reply": any(row["awaiting_reply"] for row in rows),
        "awaiting_approval": any(row["awaiting_approval"] for row in rows),
        "assigned": any(row["assigned"] for row in rows),
        "agent_state": (
            AgentState.working.value
            if AgentState.working.value in states
            else AgentState.errored.value
            if AgentState.errored.value in states
            else AgentState.idle.value
        ),
    }


async def add_work_attention(
    conn: sqlite3.Connection,
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
    *,
    tickets: Iterable[JsonDict] = (),
    sprint_items: Iterable[JsonDict] = (),
    approval_holder: Principal = OWNER_PRINCIPAL,
) -> None:
    """Attach attention for the viewing holder and shared agent-state facts."""
    ticket_rows = tuple(tickets)
    item_rows = tuple(sprint_items)
    direct_ticket_ids = {str(row["id"]) for row in ticket_rows}
    item_ids = {str(row["id"]) for row in item_rows}
    child_ids_by_item: dict[str, list[str]] = {item_id: [] for item_id in item_ids}
    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        for row in conn.execute(
            f"SELECT id, sprint_item_id FROM tickets WHERE sprint_item_id IN ({placeholders})",
            tuple(sorted(item_ids)),
        ).fetchall():
            child_ids_by_item[str(row["sprint_item_id"])].append(str(row["id"]))
    all_ticket_ids = direct_ticket_ids | {
        ticket_id for ticket_ids in child_ids_by_item.values() for ticket_id in ticket_ids
    }
    stored_tickets = _ticket_rows(conn, all_ticket_ids)
    item_conversations: dict[str, str] = {}
    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            "SELECT si.id, a.conversation_id FROM sprint_items si LEFT JOIN agents a "
            "ON a.agent_key = si.supervisor_agent_key "
            f"WHERE si.id IN ({placeholders})",
            tuple(sorted(item_ids)),
        ).fetchall()
        item_conversations = {
            str(row["id"]): str(row["conversation_id"])
            for row in rows
            if row["conversation_id"] is not None
        }
    conversation_ids = {
        str(row["conversation_id"])
        for row in stored_tickets.values()
        if row["conversation_id"] is not None
    } | set(item_conversations.values())
    conversations = await _conversation_attention(
        conversation_system, conversation_record, conversation_ids
    )
    blocked_ticket_ids = ticket_blocks.blocked_ticket_ids(conn)
    ticket_attention = {
        ticket_id: _ticket_attention(
            row,
            conversations.get(str(row["conversation_id"]))
            if row["conversation_id"] is not None
            else None,
            facts=derivation.derive_ticket_facts(
                derivation.stored_facts_from_row(
                    row, has_live_blocker=ticket_id in blocked_ticket_ids
                )
            ),
            approval_holder=approval_holder,
        )
        for ticket_id, row in stored_tickets.items()
    }
    for row in ticket_rows:
        row.update(ticket_attention.get(str(row["id"]), _empty_attention()))
    for row in item_rows:
        item_id = str(row["id"])
        own_conversation = conversations.get(item_conversations.get(item_id, ""))
        awaiting_reply, running, failed = own_conversation or (False, False, False)
        row.update(
            {
                "awaiting_reply": awaiting_reply,
                "awaiting_approval": False,
                "assigned": False,
                "agent_state": (
                    AgentState.working.value
                    if running
                    else AgentState.errored.value
                    if failed
                    else AgentState.idle.value
                ),
                "ticket_rollup": _roll_up(
                    ticket_attention[ticket_id] for ticket_id in child_ids_by_item.get(item_id, ())
                ),
            }
        )
