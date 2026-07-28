"""Day domain shapes: the day row, the day-ticket association, and the
planning-date function signature. Stdlib only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypedDict


@dataclass
class Day:                         # §3.4 — overview = four structured day fields
    id: str                        # day_YYYY-MM-DD (planning date)
    focus: str                     # the one-line hero (plain text)
    brief_take: str                # markdown
    watchout: str                  # markdown
    if_today_lands: str            # markdown
    midday_reconciliation: str     # markdown; mid-day reality, not a replacement plan
    notes: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class DayTicket:                   # day_tickets row (§3.4)
    day_id: str
    ticket_id: str
    position: int                  # contiguous from 0


# --- request bodies (§9 wire shapes) ---
# Every key is optional on the wire: an absent key takes the documented default,
# unknown keys are ignored. The api layer marshals the raw JSON dict into these
# shapes; a null or wrong-typed value raises ErrorCode.validation.


class DayPatchBody(TypedDict, total=False):       # PATCH /day/{date} — each field edits alone
    focus: str | None              # absent/null = leave unchanged
    brief_take: str | None         # absent/null = leave unchanged
    watchout: str | None           # absent/null = leave unchanged
    if_today_lands: str | None     # absent/null = leave unchanged
    midday_reconciliation: str | None
    notes: str | None              # absent/null = leave unchanged


class AddDayTicketBody(TypedDict, total=False):   # POST /day/{date}/tickets
    ticket_id: str                 # required (default "" fails the existence guard)


# Planning-date math (§6.1): implemented in days/logic/dates.py at stage 3.
# Signature is the contract: planning_date(now, boundary_hour) -> calendar date of
# (now - boundary_hour hours).
PlanningDateFn = Callable[[datetime, int], date]
