"""Coherent manual snapshots and the canonical atomic manual writer."""

import sqlite3
from dataclasses import asdict

from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.measures import data
from planner.measures.actions import _require_author
from planner.measures.contracts import (
    ManualMeasureState,
    ManualWriteBody,
    ManualWriteReceipt,
    ManualWriteResult,
    Measure,
    MeasureOrigin,
)
from planner.measures.logic.dates import calendar_date, invalid, local_date
from planner.measures.logic.manual import accepted_value, fingerprint, parse_write


def _snapshot(conn: sqlite3.Connection, measure: Measure, day: str, now: int) -> ManualMeasureState:
    if measure.origin is not MeasureOrigin.manual:
        raise invalid("manual controls require a manual measure")
    calendar_date(day)
    row = conn.execute(
        "SELECT * FROM measure_observations WHERE measure_id=? AND date<=? "
        "ORDER BY date DESC LIMIT 1",
        (measure.id, day),
    ).fetchone()
    latest = data._observation(row) if row is not None else None
    return ManualMeasureState(
        measure.id,
        measure.definition_revision,
        measure.write_revision,
        measure.timezone,
        local_date(now, measure.timezone).isoformat(),
        day,
        latest if latest is not None and latest.date == day else None,
        latest,
    )


def read_state(
    conn: sqlite3.Connection, measure_id: str, day: str | None, *, now: int
) -> ManualMeasureState:
    with data.transaction(conn, read_only=True):
        measure = data.read_measure(conn, measure_id)
        selected_date = day if day is not None else local_date(now, measure.timezone).isoformat()
        return _snapshot(conn, measure, selected_date, now)


def _receipt(row: sqlite3.Row) -> ManualWriteReceipt:
    return ManualWriteReceipt(
        row["request_id"],
        row["measure_id"],
        Principal(PrincipalKind(row["actor_kind"]), row["actor_id"]),
        row["payload_fingerprint"],
        row["date"],
        float(row["value"]),
        int(row["write_revision"]),
        int(row["accepted_at"]),
    )


def _require_manual_author(conn: sqlite3.Connection, principal: Principal, measure_id: str) -> None:
    if principal.kind is not PrincipalKind.goal:
        _require_author(conn, principal, None)
        return
    from planner.goals import data as goals
    from planner.goals.logic.documents import measure_ids

    goal = goals.read_goal(conn, principal.id)
    measure = data.read_measure(conn, measure_id)
    if measure.origin is not MeasureOrigin.manual or measure_id not in measure_ids(
        goal.primary_chart, goal.page
    ):
        raise PlannerError(
            ErrorCode.agent_forbidden, "goal can write only its bound manual measures"
        )


def write_value(
    conn: sqlite3.Connection,
    measure_id: str,
    body: ManualWriteBody,
    *,
    principal: Principal,
    now: int,
) -> ManualWriteResult:
    request = parse_write(body)
    payload_fingerprint = fingerprint(measure_id, request)
    with data.transaction(conn):
        # Current capability applies even when a previous request receipt exists.
        _require_manual_author(conn, principal, measure_id)
        accepted = conn.execute(
            "SELECT * FROM manual_measure_writes WHERE request_id=?", (request["request_id"],)
        ).fetchone()
        if accepted is not None:
            receipt = _receipt(accepted)
            if receipt.actor != principal or receipt.payload_fingerprint != payload_fingerprint:
                raise PlannerError(ErrorCode.stale_claim, "manual request ID was already used")
            state = _snapshot(conn, data.read_measure(conn, measure_id), request["date"], now)
            return ManualWriteResult(receipt, state, True)
        measure = data.read_measure(conn, measure_id)
        state = _snapshot(conn, measure, request["date"], now)
        if request["expected_definition_revision"] != measure.definition_revision:
            raise PlannerError(
                ErrorCode.stale_claim, "measure definition changed", {"state": asdict(state)}
            )
        if (
            request["operation"] == "replace"
            and request["expected_write_revision"] != measure.write_revision
        ):
            raise PlannerError(
                ErrorCode.stale_claim, "measure value changed", {"state": asdict(state)}
            )
        base = state.observation if request["basis"] == "date" else state.latest_observation
        value = accepted_value(request, base)
        receipt = ManualWriteReceipt(
            request["request_id"],
            measure_id,
            principal,
            payload_fingerprint,
            request["date"],
            value,
            measure.write_revision + 1,
            now,
        )
        conn.execute(
            "INSERT INTO manual_measure_writes(request_id,measure_id,actor_kind,actor_id,"
            "payload_fingerprint,date,value,write_revision,accepted_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                receipt.request_id,
                receipt.measure_id,
                principal.kind.value,
                principal.id,
                receipt.payload_fingerprint,
                receipt.date,
                receipt.value,
                receipt.write_revision,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,"
            "definition_revision,manual_write_id) VALUES(?,?,?,NULL,?,?,?) "
            "ON CONFLICT(measure_id,date) DO UPDATE SET value=excluded.value,run_id=NULL,"
            "observed_at=excluded.observed_at,definition_revision=excluded.definition_revision,"
            "manual_write_id=excluded.manual_write_id,period_start_date=NULL,period_end_date_exclusive=NULL",
            (measure_id, receipt.date, value, now, measure.definition_revision, receipt.request_id),
        )
        conn.execute(
            "UPDATE measures SET write_revision=?,updated_at=? WHERE id=?",
            (receipt.write_revision, now, measure_id),
        )
        state = _snapshot(conn, data.read_measure(conn, measure_id), request["date"], now)
        return ManualWriteResult(receipt, state, False)
