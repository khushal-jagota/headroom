"""Review cards preserve proposal identity and exact conversation addresses."""

from __future__ import annotations

from typing import Literal, TypedDict

from planner.navigation.views.contracts import ViewContentReference


class ReviewProposalItem(TypedDict):
    review_item_type: Literal["proposal"]
    ticket_id: str
    field: str
    title: str
    waiting_since: int


class NavigationReviewProposalItem(ReviewProposalItem):
    proposal_field: str
    proposal_revision: int


class ReviewMessageItem(TypedDict):
    review_item_type: Literal["message"]
    conversation_id: str
    address_id: str
    owner_kind: Literal["ticket", "manager", "chief"]
    owner_id: str
    title: str
    waiting_since: int
    recap: str
    recap_reference: ViewContentReference | None


class ReviewTicketItem(TypedDict):
    review_item_type: Literal["ticket"]
    ticket_id: str
    title: str
    waiting_since: int
    proposal: ReviewProposalItem | None
    messages: list[ReviewMessageItem]


class NavigationReviewTicketItem(TypedDict):
    review_item_type: Literal["ticket"]
    ticket_id: str
    title: str
    waiting_since: int
    proposal: NavigationReviewProposalItem | None
    messages: list[ReviewMessageItem]


type ReviewItem = ReviewTicketItem | ReviewMessageItem
type NavigationReviewItem = NavigationReviewTicketItem | ReviewMessageItem
