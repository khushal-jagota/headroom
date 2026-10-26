"""Pure Review eligibility and Ticket grouping rules."""

from typing import Any

from planner.navigation.review_contracts import (
    ReviewItem,
    ReviewMessageItem,
    ReviewProposalItem,
    ReviewTicketItem,
)


def message_owner_is_live(kind: str, owner: dict[str, Any]) -> bool:
    """Review alone follows the state of the conversation's canonical owner."""
    if kind == "ticket":
        return owner["stage"] != "done" and not owner["is_backlogged"]
    return kind != "manager" or not owner["settled"]


def group_review_items(items: list[ReviewProposalItem | ReviewMessageItem]) -> list[ReviewItem]:
    tickets: dict[str, ReviewTicketItem] = {}
    result: list[ReviewItem] = []
    for item in items:
        if item["review_item_type"] == "message" and item["owner_kind"] != "ticket":
            result.append(item)
            continue
        ticket_id = (
            item["ticket_id"] if item["review_item_type"] == "proposal" else item["owner_id"]
        )
        card = tickets.setdefault(
            ticket_id,
            {
                "review_item_type": "ticket",
                "ticket_id": ticket_id,
                "title": item["title"],
                "waiting_since": item["waiting_since"],
                "proposal": None,
                "messages": [],
            },
        )
        card["waiting_since"] = min(card["waiting_since"], item["waiting_since"])
        if item["review_item_type"] == "proposal":
            card["proposal"] = item
        else:
            card["messages"].append(item)
    result.extend(tickets.values())
    return sorted(
        result,
        key=lambda item: (
            item["waiting_since"],
            item["review_item_type"],
            item["conversation_id"] if item["review_item_type"] == "message" else item["ticket_id"],
        ),
    )
