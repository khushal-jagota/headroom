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

When one of these fails, that is a question and not a number to adjust. The ceilings are
today's measured cost, so a failure means a read got more expensive than it was, and the
useful first move is to find out why rather than to make room.

Sometimes the answer is that the new cost is fine, and that is a decision - write it beside
the number, with the arithmetic, the way the declined changes in this file are written. And
when a ceiling does rise, lower the others to whatever they now measure in the same change.
A gate that only ever ratchets upward one Ticket at a time stops being a gate, and nobody
notices the moment it stopped.

The growth assertion is not like the others. A statement or byte ceiling creeping up is a
cost somebody chose; that ratio moving is the original defect coming back, possibly through
a query nobody has looked at yet. It is the only check here that would notice that, so it
is the one to be slowest about relaxing.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from tests.unit.test_conversation_view_projection import FIXTURES, database
from tests.unit.test_conversation_view_storage import event, prompt

from planner.conversation.views.api import _changes
from planner.conversation.views.contracts import (
    ViewHistoryScope,
    ViewPageCursor,
    ViewRetainedRange,
)
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
        #
        # Two hundred statements for a hundred items is two per item, and that is bodies
        # being rebuilt from source events on every read. Storing the rendered bodies in a
        # side table would take it to about two statements for the page, and was planned
        # and then deliberately not built: measured, it removes about 2 ms from a page read
        # that now takes 9.6 to 11 ms on the largest real conversation, in exchange for
        # roughly 213 MB of stored bodies, a projection version bump that hard-fails every
        # client that has not reloaded, two minutes of a boot not serving, and re-render
        # logic whose failure mode is stale words on screen. That trade was worth making
        # when a page read was 26 to 53 ms and growing with the conversation; it is not
        # worth making now that the growth is gone. If this count ever has to come down,
        # that is the change, and the reason it was declined is the arithmetic above.
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


def test_sparse_cursor_and_expanded_reads_remain_bounded_as_history_grows() -> None:
    """Expiry leaves sparse sequence positions; reverse probes must stay indexed.

    The existing growth gate covers only the first timeline page of dense history.
    Reuse its source generator and Work counters for cursor and expanded scope reads.
    """
    measurements: dict[str, list[Work]] = {"sparse": [], "expanded": []}
    for turns in (TURNS, TURNS * 2):
        events = conversation(turns)
        sparse = [row for row in events if row["kind"] != "tool_call_started"]
        with database(sparse) as conn:
            first = read_page(conn, "fixture", "full")
            assert first["older"] is not None
            with Work(conn) as work:
                page = read_page(
                    conn,
                    "fixture",
                    "full",
                    cursor={**first["older"], "order": first["items"][-1]["order"]},
                )
            assert page["newer"] is not None
            assert len(work.statements) <= 210
            assert work.steps <= 16_000
            measurements["sparse"].append(work)
        with database(events) as conn:
            scope: ViewHistoryScope = {"kind": "work_group", "key": "work:e2"}
            first = read_page(conn, "fixture", "full", scope=scope)
            cursor: ViewPageCursor = {
                "projection_version": first["projection_version"],
                "lens": "full",
                "scope": scope,
                "order": first["items"][0]["order"],
            }
            with Work(conn) as work:
                page = read_page(
                    conn, "fixture", "full", scope=scope, cursor=cursor, direction="newer"
                )
            assert len(page["items"]) == TOOLS_PER_TURN - 1
            assert page["older"] is not None
            assert len(work.statements) <= 210
            assert work.steps <= 16_000
            measurements["expanded"].append(work)
    for name, (shorter, longer) in measurements.items():
        assert len(longer.statements) == len(shorter.statements), name
        assert longer.steps / max(shorter.steps, 1) <= 1.25, name


def test_reverse_availability_does_not_walk_hidden_tool_or_message_runs() -> None:
    """An arbitrary old cursor must not charge for the hidden run beside it.

    First-page and sparse-history gates never exercised this reverse existence
    boundary. Reuse the projection fixture and counters, without a browser host.
    """
    for kind in ("tool_call_started", "agent_message"):
        costs: dict[tuple[str, bool, int], list[int]] = {}
        for count in (128, 1024):
            payload = (
                {"text": "Hidden message"}
                if kind == "agent_message"
                else {
                    "tool_call_id": "tool",
                    "title": "Read",
                    "tool_kind": "other",
                    "detail": "x",
                }
            )
            events = [prompt()]
            events += [
                event(sequence, kind, {**payload, "tool_call_id": str(sequence)})
                for sequence in range(2, count + 2)
            ]
            for closed in (False, True):
                rows = events + (
                    [event(count + 2, "turn_ended", {"ending": "completed", "error_summary": None})]
                    if closed
                    else []
                )
                with database(rows) as conn:
                    for lens in ("focus", "full"):
                        for edge in (0, 2):
                            cursor: ViewPageCursor = {
                                "projection_version": 1,
                                "lens": lens,
                                "scope": {"kind": "timeline", "key": None},
                                "order": {"sequence": edge, "slot": 0},
                            }
                            with Work(conn) as work:
                                page = read_page(conn, "fixture", lens, cursor=cursor)
                            assert len(page["items"]) == (0 if edge == 0 else 2)
                            assert len(work.statements) <= 210
                            assert work.steps <= 16_000
                            costs.setdefault((lens, closed, edge), []).append(work.steps)
        for shorter, longer in costs.values():
            assert longer / max(shorter, 1) <= 1.25


def test_expanded_reverse_reads_seek_past_tools_and_addressed_messages() -> None:
    """Turn expansion cannot infer absence by walking excluded source kinds.

    The timeline growth case has aggregate witnesses that expansions lack. Reuse
    its setup and counters at both sides of an arbitrary cursor, including empty
    pages; no additional backend or browser host is needed.
    """
    owner_payload = next(
        row["payload"]
        for fixture in FIXTURES
        for row in fixture["events"]
        if row["kind"] == "message_to_owner"
    )
    scope: ViewHistoryScope = {"kind": "turn", "key": "turn:e1"}
    for kind in ("tool_call_started", "message_to_owner", "agent_message"):
        costs: dict[tuple[str, int], list[int]] = {}
        for count in (128, 1024):
            events = [prompt(), event(2, "agent_message", {"text": "Earlier"})]
            for sequence in range(3, count + 3):
                payload = (
                    owner_payload
                    if kind == "message_to_owner"
                    else {"text": "Ordinary"}
                    if kind == "agent_message"
                    else {
                        "tool_call_id": str(sequence),
                        "title": "Read",
                        "tool_kind": "other",
                        "detail": "x",
                    }
                )
                events.append(event(sequence, kind, payload))
            events += [
                event(count + 3, "agent_message", {"text": "Latest"}),
                event(count + 4, "turn_ended", {"ending": "completed", "error_summary": None}),
            ]
            with database(events) as conn:
                edges: tuple[tuple[Literal["older", "newer"], int], ...] = (
                    ("older", 4),
                    ("older", 0),
                    ("newer", count + 5),
                )
                for direction, sequence in edges:
                    cursor: ViewPageCursor = {
                        "projection_version": 1,
                        "lens": "full",
                        "scope": scope,
                        "order": {"sequence": sequence, "slot": 0},
                    }
                    with Work(conn) as work:
                        page = read_page(
                            conn, "fixture", "full", scope=scope, cursor=cursor, direction=direction
                        )
                    if sequence == 4:
                        expected = (
                            ["e2", "work:e3"]
                            if kind == "tool_call_started"
                            else ["e2"]
                            if kind == "message_to_owner"
                            else ["e2", "e3"]
                        )
                        assert [item["key"] for item in page["items"]] == expected
                    else:
                        assert page["items"] == []
                    assert len(work.statements) <= 210
                    assert work.steps <= 16_000
                    costs.setdefault((direction, sequence if sequence < 5 else -1), []).append(
                        work.steps
                    )
        for shorter, longer in costs.values():
            assert longer / max(shorter, 1) <= 1.25
