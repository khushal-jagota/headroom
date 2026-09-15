"""Stored feedback-note shapes shared across the feedback domain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeedbackState(StrEnum):
    open = "open"
    handled = "handled"


@dataclass(frozen=True, slots=True)
class FeedbackNote:
    id: str
    text: str
    page_address: str | None
    page_label: str | None
    state: FeedbackState
    ticket_id: str | None
    created_at: int
    updated_at: int
    handled_at: int | None


@dataclass(frozen=True, slots=True)
class FeedbackTicket:
    id: str
    title: str
    stage: str
    ticket_status: str


@dataclass(frozen=True, slots=True)
class HandledFeedbackGroup:
    ticket: FeedbackTicket | None
    notes: tuple[FeedbackNote, ...]
