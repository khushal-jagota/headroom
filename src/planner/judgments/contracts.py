"""Stored judgment shapes and the direct-user verdict write contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class VerdictBody(TypedDict):
    rating: int | None
    text: str | None


@dataclass(frozen=True, slots=True)
class TicketJudgment:
    ticket_id: str
    verdict_rating: int | None
    verdict_text: str | None


@dataclass(frozen=True, slots=True)
class Verdict:
    rating: int | None
    text: str | None

