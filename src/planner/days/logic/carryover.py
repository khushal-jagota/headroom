"""The boundary deterministic pass (§6.2) as pure functions over plain projected
row data. Produce the ``BoundaryInputs`` digest lists: carryover, overdue,
approvals — plus the yesterday done/not-done split for ``day_closed``. Zero DB
access; the boundary layer does the SQL and hands rows in. Stdlib + planner
contract modules only."""

from __future__ import annotations

from typing import Any

from planner.sprints.contracts import ItemStatus
from planner.tickets.contracts import GATING_FIELD, TicketState

# §6.2.2 — carryover excludes tickets that are already terminal.
_CARRYOVER_EXCLUDE = {TicketState.done, TicketState.dropped}
# Overdue excludes items that are done or already deferred to next sprint.
_ITEM_CLOSED = {ItemStatus.done, ItemStatus.deferred_next_sprint}


def carryover_candidates(yesterday_tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§6.2 step 2: yesterday's day-tickets whose ticket state is NOT done/dropped.

    Input rows carry {id, title, state, priority} (ticket joined to its day-ticket,
    in day-ticket position order). Output digest keeps the same four keys. StrEnum
    members compare equal to their string values, so plain state strings work.
    """
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "state": row["state"],
            "priority": row["priority"],
        }
        for row in yesterday_tickets
        if row["state"] not in _CARRYOVER_EXCLUDE
    ]


def day_ticket_counts(yesterday_tickets: list[dict[str, Any]]) -> tuple[int, int]:
    """§6.2 step 4: (done_count, not_done_count) over yesterday's day-tickets.

    done_count = rows with state == done; not_done_count = the rest (dropped and
    every unfinished state). The two sum to len(rows).
    """
    done_count = sum(1 for row in yesterday_tickets if row["state"] == TicketState.done)
    return done_count, len(yesterday_tickets) - done_count


def overdue_list(
    tickets: list[dict[str, Any]], items: list[dict[str, Any]], today_iso: str
) -> list[dict[str, Any]]:
    """§6.2 step 3 (overdue). A ticket is overdue iff deadline is not None and
    deadline < today_iso (strict; due-today is NOT overdue) and state not in
    {done, dropped}. An item is overdue iff deadline < today_iso and status not
    in {done, deferred_next_sprint}. Uniform digest {id, title, state, priority}
    (for items, state = the item status). ISO date strings compare lexically.
    Tickets first, then items."""
    result: list[dict[str, Any]] = []
    for row in tickets:
        deadline = row["deadline"]
        if deadline is not None and deadline < today_iso and row["state"] not in _CARRYOVER_EXCLUDE:
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "state": row["state"],
                    "priority": row["priority"],
                }
            )
    for row in items:
        deadline = row["deadline"]
        if deadline is not None and deadline < today_iso and row["status"] not in _ITEM_CLOSED:
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "state": row["status"],
                    "priority": row["priority"],
                }
            )
    return result


def approvals_digest(
    tickets: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """§6.2 step 3 / §4.5 approval-queue digest. Three sources, oldest-pending first:

    * a ticket with a non-null proposal on its current gating field
      (GATING_FIELD[state]); kind = the gating field name (e.g. "plan"),
      waiting_since = proposal["created_at"];
    * a ticket in ``needs_review`` (§4.5); kind = "review", waiting_since =
      the ticket's updated_at (DB-internal proxy for review entry time);
    * a sprint item with a non-null status_proposal; kind = "status",
      waiting_since = proposal["created_at"].

    Digest per base.py: {entity_id, kind, waiting_since}. Ticket rows carry
    {id, state, fields(dict), updated_at}; item rows carry {id, status_proposal}.
    """
    digest: list[dict[str, Any]] = []
    for row in tickets:
        state = row["state"]
        if state == TicketState.needs_review:
            digest.append(
                {"entity_id": row["id"], "kind": "review", "waiting_since": row["updated_at"]}
            )
            continue
        gating = GATING_FIELD.get(TicketState(state))
        if gating is None:
            continue
        slot = row["fields"].get(gating.value)
        if slot is None:
            continue
        proposal = slot.get("proposal")
        if proposal is None:
            continue
        digest.append(
            {"entity_id": row["id"], "kind": gating.value, "waiting_since": proposal["created_at"]}
        )
    for row in items:
        proposal = row["status_proposal"]
        if proposal is None:
            continue
        digest.append(
            {"entity_id": row["id"], "kind": "status", "waiting_since": proposal["created_at"]}
        )
    digest.sort(key=lambda entry: entry["waiting_since"])
    return digest
