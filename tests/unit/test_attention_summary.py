"""Historical figures must preserve identity, attribution, and unavailable evidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from planner.attention.contracts import (
    AttentionEvent,
    Destination,
    Evidence,
    GateInterval,
    GroupBy,
    Scope,
    ScopeKind,
    SummaryRequest,
)
from planner.attention.logic.summary import build_summary, periods


def at(day: str, hour: int = 0) -> int:
    return int(
        datetime.fromisoformat(f"{day}T{hour:02}:00:00")
        .replace(tzinfo=ZoneInfo("Europe/Berlin"))
        .timestamp()
    )


def request(**kwargs: object) -> SummaryRequest:
    return SummaryRequest(first_date=date(2026, 11, 2), last_date=date(2026, 11, 2), **kwargs)  # type: ignore[arg-type]


def ticket(identity: str, project: str = "p", manager: str | None = None) -> Destination:
    return Destination("ticket", identity, identity, f"/tickets/{identity}", project, manager)


def metric(result: dict[str, Any], name: str, period: str = "current") -> object:
    return result["totals"][period][name]["value"]


def test_grouped_figures_keep_unique_events_union_wait_and_full_page_denominators() -> None:
    first, second = ticket("first", manager="m"), ticket("second", "q")
    start = at("2026-11-02")
    evidence = Evidence(
        (
            AttentionEvent("one", start + 100, "owner_message", first),
            AttentionEvent("two", start + 200, "owner_message", first),
            AttentionEvent("approve", start + 300, "owner_approval", second),
            AttentionEvent("agent", start + 400, "activity", ticket("agent", "q")),
        ),
        (
            GateInterval("g1", first, start - 50, start - 50, start + 600),
            GateInterval("g2", second, start + 300, start + 300, start + 900),
        ),
        start - 86400 * 10,
        destinations=(Destination("project", "p", "Project P", "/projects/p", "p"),),
        message_history_started_at=start - 86400 * 10,
    )
    result = build_summary(request(limit=1), evidence, now=at("2026-11-04"))
    assert metric(result, "owner_messages") == 2
    assert metric(result, "owner_approvals") == 1
    assert metric(result, "approval_wait_seconds") == 1200
    assert metric(result, "approval_wait_union_seconds") == 900
    assert metric(result, "active_ticket_count") == 3
    assert metric(result, "without_owner_involvement_share") == pytest.approx(1 / 3)
    assert result["page"]["match_count"] == 2
    assert result["page"]["omitted_after"] == 1
    assert result["rows"][0]["title"] == "Project P"
    assert result["rows"][0]["message_share"] == 1
    assert result["rows"][0]["approval_share"] == 0


def test_global_message_identity_precedes_time_scope_and_group_selection() -> None:
    old = AttentionEvent(
        "owner:replayed", at("2026-11-01", 10), "owner_message", ticket("original", "p")
    )
    replay = AttentionEvent(
        "owner:replayed", at("2026-11-02", 10), "owner_message", ticket("copy", "q")
    )
    evidence = Evidence(
        (replay, old), (), at("2026-10-01"), message_history_started_at=at("2026-10-01")
    )
    result = build_summary(
        request(scope=Scope(ScopeKind.project, "q")), evidence, now=at("2026-11-04")
    )
    assert metric(result, "owner_messages") == 0
    assert result["rows"] == []
    all_work = build_summary(request(), evidence, now=at("2026-11-04"))
    assert metric(all_work, "owner_messages") == 0
    assert metric(all_work, "owner_messages", "previous") == 1


def test_unknown_approvals_and_unauthenticated_messages_cannot_prove_absence() -> None:
    start = at("2026-11-02")
    evidence = Evidence(
        (AttentionEvent("agent", start + 10, "activity", ticket("work")),),
        (),
        start + 100,
        message_history_started_at=start - 86400,
        unattributed_message_times=(start + 20,),
    )
    result = build_summary(request(), evidence, now=at("2026-11-04"))
    assert metric(result, "owner_messages") is None
    assert metric(result, "owner_approvals") is None
    assert metric(result, "tickets_without_owner_involvement") is None
    assert metric(result, "without_owner_involvement_share") is None
    assert result["totals"]["current"]["active_ticket_count"]["coverage_status"] == "partial"
    assert result["totals"]["difference"]["owner_approvals"]["value"] is None


def test_open_unknown_start_gate_and_direct_goal_attribution_remain_explicit() -> None:
    start = at("2026-11-02")
    goal = Destination("goal", "g", "Goal", "/goals/g", "p", goal_id="g")
    evidence = Evidence(
        (
            AttentionEvent("direct", start + 10, "owner_message", goal),
            AttentionEvent("ticket", start + 20, "owner_message", ticket("t")),
        ),
        (GateInterval("open", ticket("t"), None, start, None),),
        start,
        message_history_started_at=start - 86400,
    )
    result = build_summary(request(group_by=GroupBy.goal), evidence, now=start + 3600)
    assert metric(result, "approval_wait_seconds") == 3600
    assert result["totals"]["current"]["approval_wait_seconds"]["coverage_status"] == "partial"
    assert {row["kind"] for row in result["rows"]} == {"goal", "unattributed"}
    assert any("direct goal" in note for note in result["limitations"])


def test_local_calendar_comparisons_use_dst_boundaries_and_validate_dates() -> None:
    current, previous = periods(
        SummaryRequest(date(2026, 10, 25), date(2026, 10, 25)), now=at("2026-11-04")
    )
    assert current["end_at"] - current["start_at"] == 25 * 3600
    assert previous["end_at"] - previous["start_at"] == 24 * 3600
    assert previous["last_date"] == "2026-10-24"
    with pytest.raises(ValueError, match="together"):
        periods(SummaryRequest(first_date=date(2026, 11, 1)), now=at("2026-11-04"))
    with pytest.raises(ValueError, match="beyond today"):
        periods(SummaryRequest(date(2026, 11, 5), date(2026, 11, 5)), now=at("2026-11-04"))
