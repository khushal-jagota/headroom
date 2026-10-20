"""What one conversation read is allowed to cost, counted rather than timed.

A conversation read got expensive once without anyone noticing, and the reason it was
hard to notice is that the only evidence was a clock. Wall clock on a build machine says
almost nothing: load on the machine these budgets were measured on moved between 0.8 and
5.1 while they were being taken. So this counts the work instead — statements run, rows
visited, bytes produced — which is the same on every machine and changes only when the
code changes.

The ceilings below are today's cost, not a target. Each one comes down as the change that
earns it lands, and the number in the failure message is the point: a read that starts
costing more than it did has to be looked at, not waved through.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tests.unit.test_conversation_view_projection import database

from planner.conversation.views.api import _changes
from planner.conversation.views.contracts import ViewRetainedRange
from planner.conversation.views.live_output import ConversationViewLiveOutput
from planner.conversation.views.status import read_status
from planner.conversation.views.storage import read_page

TURNS = 40
TOOLS_PER_TURN = 6


def conversation(turns: int = TURNS) -> list[dict[str, Any]]:
    """A conversation long enough that one page is a full page under either lens."""
    events: list[dict[str, Any]] = []
    sequence = 0

    def add(kind: str, payload: dict[str, Any]) -> None:
        nonlocal sequence
        sequence += 1
        events.append(
            {"sequence": sequence, "created_at": sequence, "kind": kind, "payload": payload}
        )

    for turn in range(turns):
        add("prompt", {"sender_label": "owner", "mode": "queue", "text": f"Do thing {turn}"})
        for tool in range(TOOLS_PER_TURN):
            add(
                "tool_call_started",
                {
                    "tool_call_id": f"tool-{turn}-{tool}",
                    "title": "Read",
                    "tool_kind": "other",
                    "detail": "x" * 2048,
                },
            )
        add("agent_message", {"text": f"Done with thing {turn}. " + "y" * 400})
        add("turn_ended", {"ending": "completed", "error_summary": None})
    return events


class Work:
    """Statements and VM steps charged to one read."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.statements: list[str] = []
        self.steps = 0

    def __enter__(self) -> Work:
        self.conn.set_trace_callback(self.statements.append)
        # A progress handler every thousand steps is cheap and precise enough to notice a
        # query that started scanning a table it used to seek into.
        self.conn.set_progress_handler(self._tick, 1000)
        return self

    def _tick(self) -> int:
        self.steps += 1000
        return 0

    def __exit__(self, *_: object) -> None:
        self.conn.set_progress_handler(None, 0)
        self.conn.set_trace_callback(None)


def test_a_focus_page_costs_what_it_costs_today() -> None:
    with database(conversation()) as conn:
        with Work(conn) as work:
            page = read_page(conn, "fixture", "focus")
        bytes_out = len(json.dumps(page))
        assert len(page["items"]) > 0
        # Today: 80 items, 162 statements, 7,000 steps, 31,229 bytes. The statement count
        # is this high because every body is rebuilt from source events a row at a time.
        assert len(work.statements) <= 170, (
            f"a Focus page now runs {len(work.statements)} statements"
        )
        assert work.steps <= 9_000, f"a Focus page now visits {work.steps} VM steps"
        assert bytes_out <= 35_000, f"a Focus page now produces {bytes_out} bytes"


def test_a_full_page_costs_what_it_costs_today() -> None:
    with database(conversation()) as conn:
        with Work(conn) as work:
            page = read_page(conn, "fixture", "full")
        bytes_out = len(json.dumps(page))
        assert len(page["items"]) > 0
        # Today: 100 items, 202 statements, 13,000 steps, 50,994 bytes. It was 276 before
        # the conversation's backend stopped being re-read once per row.
        assert len(work.statements) <= 210, (
            f"a Full page now runs {len(work.statements)} statements"
        )
        # This was 17,000 before the candidate query was driven off the open-group index
        # instead of the rows table. The headroom is for planner differences between
        # SQLite versions, not for new work.
        assert work.steps <= 16_000, f"a Full page now visits {work.steps} VM steps"
        assert bytes_out <= 60_000, f"a Full page now produces {bytes_out} bytes"


def test_one_tail_wake_costs_what_it_costs_today() -> None:
    """What a conversation sitting open charges per change, which is the cost per row.

    A live conversation wakes its readers once per appended row, so this number is paid
    hundreds of times in a session where a page read is paid once.
    """
    events = conversation()
    with database(events) as conn:
        through = events[-1]["sequence"]
        conn.execute(
            f"ALTER TABLE conversations ADD COLUMN latest_sequence INTEGER DEFAULT {through}"
        )
        live = ConversationViewLiveOutput()
        page = read_page(conn, "fixture", "focus")
        items = page["items"]
        ranges: list[ViewRetainedRange] = [
            {"scope": page["scope"], "first": items[0]["order"], "last": items[-1]["order"]}
        ]
        with Work(conn) as work:
            status = read_status(conn, "fixture", "focus", live, running=True)
            changes = _changes(conn, "fixture", "focus", through, ranges, status)
        assert changes is not None
        bytes_out = len(json.dumps(changes))
        # Today: 11 statements and 1,406 bytes for a wake carrying no change at all, of
        # which the status block is 1,056 — sent whether or not it moved.
        assert changes["upserts"] == []
        assert len(work.statements) <= 12, f"one wake now runs {len(work.statements)} statements"
        assert bytes_out <= 1_600, f"one wake now sends {bytes_out} bytes with nothing changed"


def test_a_bounded_page_does_not_cost_more_because_the_conversation_is_longer() -> None:
    """The defect this Ticket found, and the gate that keeps it fixed.

    A page is bounded at a hundred items, so reading one should cost what a hundred items
    cost and nothing more. Under the Full lens it did not: the candidate query visited the
    conversation rather than the page. Measured here before the fix, 13,000 VM steps at 360
    events rising to 63,000 at 2,880; after it, 14,000 rising to 18,000. On the largest real
    conversation, 7,108 projected rows with 1,175 work groups of which 2 were still open,
    the Full candidate query went from 176,000 VM steps to 15,000 and the Focus one from
    34,000 to 8,000.

    The fix costs about a thousand steps on a short conversation, which is the per-member
    bound being set up, and it stops mattering immediately: the old query is already more
    expensive by 720 events.
    """
    short = conversation()
    long = conversation(turns=TURNS * 2)
    with database(short) as conn:
        with Work(conn) as shorter:
            short_page = read_page(conn, "fixture", "full")
    with database(long) as conn:
        with Work(conn) as longer:
            long_page = read_page(conn, "fixture", "full")

    assert len(short_page["items"]) == len(long_page["items"])
    assert len(shorter.statements) == len(longer.statements), (
        "the statement count is per item, so it must not move when only the conversation does"
    )
    growth = longer.steps / max(shorter.steps, 1)
    assert growth <= 1.25, (
        f"doubling the conversation multiplied a bounded page read by {growth:.2f}: "
        f"{shorter.steps} VM steps at {len(short)} events, {longer.steps} at {len(long)}"
    )
