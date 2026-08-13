"""Stored Ticket judgment shapes and write contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class VerdictBody(TypedDict):
    rating: int | None
    text: str | None


class TroubleNoteBody(TypedDict):
    body: str


@dataclass(frozen=True, slots=True)
class TroubleNote:
    sequence: int
    body: str
    created_at: int


@dataclass(frozen=True, slots=True)
class TicketJudgment:
    ticket_id: str
    verdict_rating: int | None
    verdict_text: str | None
    trouble_notes: tuple[TroubleNote, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    rating: int | None
    text: str | None
