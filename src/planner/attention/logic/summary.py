"""Pure historical attention windows, attribution, and aggregation."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from planner.attention.configuration import DEFAULT_DAYS, SORT_FIELDS, TIMEZONE
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
from planner.core.contracts import JsonDict
from planner.list_reads.contracts import ListPage

METRICS = (
    "owner_messages",
    "owner_approvals",
    "approval_wait_seconds",
    "approval_wait_union_seconds",
    "active_ticket_count",
    "tickets_without_owner_involvement",
    "without_owner_involvement_share",
)


def periods(request: SummaryRequest, *, now: int) -> tuple[JsonDict, JsonDict]:
    """Validate the request and resolve inclusive local dates to half-open timestamps."""
    today = datetime.fromtimestamp(now, ZoneInfo(TIMEZONE)).date()
    if (request.first_date is None) != (request.last_date is None):
        raise ValueError("first_date and last_date must be supplied together")
    first = request.first_date or today - timedelta(days=DEFAULT_DAYS - 1)
    last = request.last_date or today
    if first > last or last > today:
        raise ValueError("dates must be ordered and must not extend beyond today")
    if request.sort not in SORT_FIELDS:
        raise ValueError("unsupported attention sort")
    if request.limit < 1 or request.offset < 0:
        raise ValueError("limit must be positive and offset must be nonnegative")
    if request.scope.kind in (ScopeKind.all, ScopeKind.chief):
        if request.scope.id is not None:
            raise ValueError("all and chief scopes do not accept an id")
    elif not request.scope.id:
        raise ValueError("this scope requires an id")
    try:
        GroupBy(request.group_by)
        ScopeKind(request.scope.kind)
        width = (last - first).days + 1
        previous_last = first - timedelta(days=1)
        previous_first = first - timedelta(days=width)
        return _period(first, last, now), _period(previous_first, previous_last, now)
    except (OverflowError, ValueError) as error:
        raise ValueError("invalid attention date range or grouping") from error


def _period(first: date, last: date, now: int) -> JsonDict:
    zone = ZoneInfo(TIMEZONE)
    start = int(datetime.combine(first, time.min, zone).timestamp())
    full_end = int(datetime.combine(last + timedelta(days=1), time.min, zone).timestamp())
    return {
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "timezone": TIMEZONE,
        "start_at": start,
        "end_at": min(full_end, now),
        "partial": full_end > now,
    }


def matches(destination: Destination, scope: Scope) -> bool:
    if scope.kind == ScopeKind.all:
        return True
    if scope.kind == ScopeKind.chief:
        return destination.kind == "chief"
    if scope.kind == ScopeKind.project:
        return destination.project_id == scope.id
    if scope.kind == ScopeKind.manager:
        return destination.manager_id == scope.id or (
            destination.kind == "manager" and destination.id == scope.id
        )
    return destination.kind == scope.kind and destination.id == scope.id


def _group(destination: Destination, by: GroupBy) -> tuple[str, str]:
    relation = {
        GroupBy.project: destination.project_id,
        GroupBy.manager: destination.manager_id
        or (destination.id if destination.kind == "manager" else None),
        GroupBy.ticket: destination.id if destination.kind == "ticket" else None,
        GroupBy.goal: destination.id if destination.kind == "goal" else None,
    }[by]
    if relation is not None:
        return str(by), relation
    if by is GroupBy.ticket and destination.kind != "ticket":
        return destination.kind, destination.id
    return "unattributed", "unattributed"


def _metric(value: int | float | None, status: str = "complete", *limitations: str) -> JsonDict:
    return {"value": value, "coverage_status": status, "limitations": list(limitations)}


def _union(intervals: list[tuple[int, int]]) -> int:
    end: int | None = None
    result = 0
    for start, stop in sorted(intervals):
        result += max(stop - (start if end is None else max(start, end)), 0)
        end = stop if end is None else max(end, stop)
    return result


def _figures(
    events: list[AttentionEvent], gates: list[GateInterval], period: JsonDict, evidence: Evidence
) -> JsonDict:
    start, end = period["start_at"], period["end_at"]
    active_events = [event for event in events if start <= event.occurred_at < end]
    intervals: list[tuple[int, int]] = []
    ticket_ids = {
        event.destination.id for event in active_events if event.destination.kind == "ticket"
    }
    involved = {
        event.destination.id
        for event in active_events
        if event.destination.kind == "ticket" and event.kind in ("owner_message", "owner_approval")
    }
    for gate in gates:
        known_start = gate.opened_at if gate.opened_at is not None else gate.observed_since
        lo, hi = (
            max(start, known_start),
            min(end, gate.closed_at if gate.closed_at is not None else end),
        )
        if lo < hi:
            intervals.append((lo, hi))
            ticket_ids.add(gate.destination.id)
    messages = sum(event.kind == "owner_message" for event in active_events)
    approvals = sum(event.kind == "owner_approval" for event in active_events)
    result = {
        "owner_messages": _metric(messages),
        "owner_approvals": _metric(approvals),
        "approval_wait_seconds": _metric(sum(hi - lo for lo, hi in intervals)),
        "approval_wait_union_seconds": _metric(_union(intervals)),
        "active_ticket_count": _metric(len(ticket_ids)),
        "tickets_without_owner_involvement": _metric(len(ticket_ids - involved)),
        "without_owner_involvement_share": _metric(
            len(ticket_ids - involved) / len(ticket_ids) if ticket_ids else None
        ),
    }
    message_unknown = (
        evidence.message_history_started_at is None or start < evidence.message_history_started_at
    )
    missing_senders = any(start <= at < end for at in evidence.unattributed_message_times)
    historical_unknown = start < evidence.history_started_at
    if message_unknown or missing_senders:
        result["owner_messages"] = _metric(
            messages if messages else None,
            "partial" if messages else "unknown",
            "Retained message history or authenticated sender metadata "
            "is incomplete for this period.",
        )
    if historical_unknown:
        for key in ("owner_approvals", "approval_wait_seconds", "approval_wait_union_seconds"):
            result[key] = _metric(
                None, "unknown", "This period predates complete approval and gate history."
            )
    if message_unknown or missing_senders or historical_unknown:
        for key in ("tickets_without_owner_involvement", "without_owner_involvement_share"):
            result[key] = _metric(
                None,
                "unknown",
                "Absence of owner involvement cannot be established from incomplete history.",
            )
        result["active_ticket_count"]["coverage_status"] = "partial"
        result["active_ticket_count"]["limitations"].append(
            "The activity denominator can omit unavailable history."
        )
    if period["partial"]:
        for metric in result.values():
            if metric["coverage_status"] == "complete":
                metric["coverage_status"] = "partial"
            metric["limitations"].append("The current local date is incomplete.")
    return result


def _comparison(current: JsonDict, previous: JsonDict) -> JsonDict:
    difference: JsonDict = {}
    for key in METRICS:
        left, right = current[key], previous[key]
        status = (
            "complete"
            if left["coverage_status"] == right["coverage_status"] == "complete"
            else "partial"
        )
        value = (
            None
            if left["value"] is None or right["value"] is None
            else left["value"] - right["value"]
        )
        difference[key] = _metric(
            value,
            "unknown" if value is None else status,
            *dict.fromkeys(left["limitations"] + right["limitations"]),
        )
    return {"current": current, "previous": previous, "difference": difference}


def build_summary(request: SummaryRequest, evidence: Evidence, *, now: int) -> JsonDict:
    current_period, previous_period = periods(request, now=now)
    # Global identity comes before date, scope, and group selection. The earliest record wins.
    unique: dict[str, AttentionEvent] = {}
    for event in sorted(evidence.events, key=lambda item: (item.occurred_at, item.identity)):
        unique.setdefault(event.identity, event)
    events = [event for event in unique.values() if matches(event.destination, request.scope)]
    gates = [gate for gate in evidence.gates if matches(gate.destination, request.scope)]
    totals = _comparison(
        _figures(events, gates, current_period, evidence),
        _figures(events, gates, previous_period, evidence),
    )
    groups: dict[tuple[str, str], Destination] = {}
    bounds = previous_period["start_at"], current_period["end_at"]
    by = GroupBy(request.group_by)
    for event in events:
        if bounds[0] <= event.occurred_at < bounds[1]:
            groups.setdefault(_group(event.destination, by), event.destination)
    for gate in gates:
        if (gate.closed_at if gate.closed_at is not None else now) > bounds[0] and (
            gate.opened_at if gate.opened_at is not None else gate.observed_since
        ) < bounds[1]:
            groups.setdefault(_group(gate.destination, by), gate.destination)
    labels = {(item.kind, item.id): item for item in evidence.destinations}
    rows: list[JsonDict] = []
    for key, source in groups.items():
        kind, identifier = key
        label = labels.get(key)
        if kind == "unattributed":
            title, url = "Unattributed", None
        elif label is not None:
            title, url = label.title, label.url
        elif key == (source.kind, source.id):
            title, url = source.title, source.url
        else:
            title, url = f"{kind.title()} {identifier}", None
        group_events = [event for event in events if _group(event.destination, by) == key]
        group_gates = [gate for gate in gates if _group(gate.destination, by) == key]
        group_destinations = [
            event.destination
            for event in group_events
            if bounds[0] <= event.occurred_at < bounds[1]
        ] + [
            gate.destination
            for gate in group_gates
            if (gate.closed_at if gate.closed_at is not None else now) > bounds[0]
            and (gate.opened_at if gate.opened_at is not None else gate.observed_since) < bounds[1]
        ]
        row = {
            "kind": kind,
            "id": identifier,
            "title": title,
            "url": url,
            "attribution_basis": sorted({item.attribution_basis for item in group_destinations}),
            **_comparison(
                _figures(group_events, group_gates, current_period, evidence),
                _figures(group_events, group_gates, previous_period, evidence),
            ),
        }
        for metric, share in (
            ("owner_messages", "message_share"),
            ("owner_approvals", "approval_share"),
        ):
            numerator, denominator = (
                row["current"][metric]["value"],
                totals["current"][metric]["value"],
            )
            row[share] = numerator / denominator if numerator is not None and denominator else None
        for relation in ("project_id", "manager_id", "goal_id"):
            values = {getattr(item, relation) for item in group_destinations}
            row[relation] = next(iter(values)) if len(values) == 1 else None
        rows.append(row)
    rows.sort(
        key=lambda row: (-(row["current"][request.sort]["value"] or 0), row["kind"], row["id"])
    )
    page = ListPage(
        tuple(rows[request.offset : request.offset + request.limit]),
        len(rows),
        request.limit,
        request.offset,
    )
    limitations = list(evidence.limitations)
    if by is GroupBy.goal or request.scope.kind is ScopeKind.goal:
        limitations.append(
            "Goal figures cover direct goal conversations only. "
            "Ticket-to-goal membership is unavailable."
        )
    return {
        "period": current_period,
        "previous_period": previous_period,
        "scope": asdict(request.scope),
        "group_by": str(by),
        "totals": totals,
        "rows": list(page.rows),
        "page": page.facts_json(),
        "snapshot_at": now,
        "reliable_history_start": {
            "messages": evidence.message_history_started_at,
            "approvals_and_gates": evidence.history_started_at,
        },
        "limitations": limitations,
    }
