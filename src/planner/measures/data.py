"""Bounded SQLite reads and transactional observation intake."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.list_reads.contracts import ListPage
from planner.measures import configuration as limits
from planner.measures.contracts import (
    CandidateObservation,
    CountPeriod,
    HistoricalUnavailableReason,
    HistoryWindow,
    Measure,
    MeasureDate,
    MeasureKind,
    MeasureOrigin,
    Observation,
    OutputMeasure,
    PeriodInterval,
)
from planner.measures.logic.dates import (
    calendar_date,
    complete_interval,
    date_range,
    invalid,
    latest_complete_date,
    local_date,
)
from planner.measures.logic.formulas import dependencies, number, parse_formula


@contextmanager
def transaction(conn: sqlite3.Connection, *, read_only: bool = False) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT measure_action")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO measure_action")
            conn.execute("RELEASE measure_action")
            raise
        else:
            conn.execute("RELEASE measure_action")
        return
    conn.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def measure_json(measure: Measure) -> JsonDict:
    return asdict(measure)


def observation_json(observation: Observation) -> JsonDict:
    return asdict(observation)


def _measure(row: sqlite3.Row) -> Measure:
    return Measure(
        str(row["id"]),
        str(row["project_id"]),
        str(row["name"]),
        str(row["unit"]),
        MeasureOrigin(row["origin"]),
        MeasureKind(row["kind"]),
        str(row["timezone"]),
        CountPeriod(row["period"]) if row["period"] else None,
        row["source_id"],
        parse_formula(json.loads(row["formula"])) if row["formula"] is not None else None,
        int(row["definition_revision"]),
        int(row["created_at"]),
        int(row["updated_at"]),
    )


def read_measure(conn: sqlite3.Connection, measure_id: str) -> Measure:
    row = conn.execute("SELECT * FROM measures WHERE id=?", (measure_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "measure not found")
    return _measure(row)


def list_measures(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    source_id: str | None,
    limit: int,
    offset: int,
) -> ListPage[Measure]:
    validate_page(limit, offset)
    where, args = [], []
    for key, value in (("project_id", project_id), ("source_id", source_id)):
        if value is not None:
            where.append(f"{key}=?")
            args.append(value)
    clause = " WHERE " + " AND ".join(where) if where else ""
    count = int(conn.execute("SELECT COUNT(*) FROM measures" + clause, args).fetchone()[0])
    rows = conn.execute(
        "SELECT * FROM measures" + clause + " ORDER BY id LIMIT ? OFFSET ?", (*args, limit, offset)
    )
    return ListPage(tuple(_measure(row) for row in rows), count, limit, offset)


def validate_page(limit: int, offset: int) -> None:
    if (
        type(limit) is not int
        or not 1 <= limit <= limits.MAX_LIST_LIMIT
        or type(offset) is not int
        or offset < 0
    ):
        raise invalid("invalid bounded list request")


def read_source_outputs(conn: sqlite3.Connection, source_id: str) -> tuple[Measure, ...]:
    rows = tuple(
        _measure(row)
        for row in conn.execute(
            "SELECT * FROM measures WHERE source_id=? ORDER BY id LIMIT ?",
            (source_id, limits.MAX_SOURCE_OUTPUTS + 1),
        )
    )
    if len(rows) > limits.MAX_SOURCE_OUTPUTS:
        raise invalid("source output limit exceeded")
    return rows


def _observation(row: sqlite3.Row) -> Observation:
    interval = (
        None
        if row["period_start_date"] is None
        else PeriodInterval(row["period_start_date"], row["period_end_date_exclusive"])
    )
    return Observation(
        row["measure_id"],
        row["date"],
        float(row["value"]),
        row["run_id"],
        int(row["observed_at"]),
        int(row["definition_revision"]),
        interval,
    )


def latest_observations(
    conn: sqlite3.Connection, measure_ids: tuple[str, ...]
) -> tuple[Observation, ...]:
    if len(measure_ids) > limits.MAX_SOURCE_OUTPUTS:
        raise invalid("source output limit exceeded")
    result = []
    for identifier in measure_ids:
        row = conn.execute(
            "SELECT * FROM measure_observations WHERE measure_id=? ORDER BY date DESC LIMIT 1",
            (identifier,),
        ).fetchone()
        if row is not None:
            result.append(_observation(row))
    return tuple(result)


def read_observations(
    conn: sqlite3.Connection, measure_id: str, first_date: str, last_date: str
) -> tuple[Observation, ...]:
    date_range(first_date, last_date, maximum=limits.MAX_HISTORY_DATES)
    rows = conn.execute(
        "SELECT * FROM measure_observations WHERE measure_id=? AND date>=? AND "
        "date<=? ORDER BY date",
        (measure_id, first_date, last_date),
    )
    return tuple(_observation(row) for row in rows)


def read_demanded(
    conn: sqlite3.Connection, pairs: set[MeasureDate], definitions: dict[str, Measure]
) -> dict[MeasureDate, Observation]:
    result: dict[MeasureDate, Observation] = {}
    for identifier, measure in definitions.items():
        if measure.origin is not MeasureOrigin.taken:
            continue
        days = sorted(pair.date for pair in pairs if pair.measure_id == identifier)
        for start in range(0, len(days), limits.SQL_IN_CHUNK):
            chunk = days[start : start + limits.SQL_IN_CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT * FROM measure_observations WHERE measure_id=? AND date IN ({marks})",
                (identifier, *chunk),
            )
            for row in rows:
                observation = _observation(row)
                result[MeasureDate(identifier, observation.date)] = observation
    return result


def read_definitions(
    conn: sqlite3.Connection, roots: tuple[str, ...], *, proposed: Measure | None = None
) -> dict[str, Measure]:
    result: dict[str, Measure] = {}
    pending = list(roots)
    while pending:
        identifier = pending.pop()
        if identifier in result:
            continue
        if len(result) >= limits.MAX_REACHABLE_MEASURES:
            raise invalid("measure dependency graph exceeds its limit")
        current = (
            proposed
            if proposed is not None and identifier == proposed.id
            else read_measure(conn, identifier)
        )
        result[identifier] = current
        if current.formula is not None:
            pending.extend(dependencies(current.formula))
    return result


def prepare_outputs(
    conn: sqlite3.Connection, source_id: str, *, frozen_at: int
) -> tuple[OutputMeasure, ...]:
    outputs = []
    for measure in read_source_outputs(conn, source_id):
        period = measure.period
        day = (
            latest_complete_date(frozen_at, measure.timezone, period)
            if period is not None
            else local_date(frozen_at, measure.timezone).isoformat()
        )
        interval = complete_interval(day, period, measure.timezone, frozen_at) if period else None
        outputs.append(
            OutputMeasure(
                measure.id,
                measure.definition_revision,
                measure.kind,
                measure.timezone,
                period,
                day,
                interval,
            )
        )
    return tuple(outputs)


def validate_batch(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    frozen_at: int,
    outputs: tuple[OutputMeasure, ...],
    candidates: tuple[CandidateObservation, ...],
    history: HistoryWindow | None,
) -> None:
    if not outputs or len(candidates) > limits.MAX_BATCH_ROWS:
        raise invalid("invalid source output batch")
    expected = prepare_outputs(conn, source_id, frozen_at=frozen_at)
    if {out.measure_id: out for out in outputs} != {out.measure_id: out for out in expected} or len(
        outputs
    ) != len(expected):
        raise invalid("stale measure definition")
    by_id = {out.measure_id: out for out in outputs}
    allowed_history = (
        set(date_range(history.first_date, history.last_date, maximum=limits.MAX_HISTORY_DATES))
        if history
        else set()
    )
    seen: set[MeasureDate] = set()
    real: set[MeasureDate] = set()
    totals: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        pair = MeasureDate(candidate.measure_id, candidate.date)
        if pair in seen or candidate.measure_id not in by_id:
            raise invalid("duplicate or foreign source output")
        seen.add(pair)
        output = by_id[candidate.measure_id]
        calendar_date(candidate.date)
        if candidate.date != output.required_date and candidate.date not in allowed_history:
            raise invalid("output date is outside admitted history")
        if candidate.date > local_date(frozen_at, output.timezone).isoformat():
            raise invalid("output date is in the future")
        if output.period:
            complete_interval(candidate.date, output.period, output.timezone, frozen_at)
        if candidate.value is None:
            if (
                candidate.date == output.required_date
                or candidate.date not in allowed_history
                or not isinstance(candidate.unavailable_reason, HistoricalUnavailableReason)
            ):
                raise invalid("required output cannot be unavailable")
            continue
        if candidate.unavailable_reason is not None:
            raise invalid("value cannot carry an unavailable reason")
        value = number(
            candidate.value,
            count=output.kind is MeasureKind.period_count,
            nonnegative=output.kind is not MeasureKind.reading,
        )
        real.add(pair)
        if output.kind is MeasureKind.running_total:
            totals.setdefault(output.measure_id, {})[candidate.date] = value
    if any(MeasureDate(out.measure_id, out.required_date) not in real for out in outputs):
        raise invalid("missing required source output")
    for identifier, changes in totals.items():
        merged = dict(changes)
        for day in changes:
            for comparison, order in (("<", "DESC"), (">", "ASC")):
                row = conn.execute(
                    f"SELECT date,value FROM measure_observations WHERE measure_id=? AND date "
                    f"{comparison} ? ORDER BY date {order} LIMIT 1",
                    (identifier, day),
                ).fetchone()
                if row is not None and row["date"] not in changes:
                    merged[row["date"]] = float(row["value"])
        values = [merged[day] for day in sorted(merged)]
        if any(left > right for left, right in zip(values, values[1:], strict=False)):
            raise invalid("running total cannot decrease")


def commit_observations(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_id: str,
    frozen_at: int,
    outputs: tuple[OutputMeasure, ...],
    candidates: tuple[CandidateObservation, ...],
    history: HistoryWindow | None,
) -> int:
    if not conn.in_transaction:
        raise RuntimeError("observation commit requires caller transaction")
    validate_batch(
        conn,
        source_id=source_id,
        frozen_at=frozen_at,
        outputs=outputs,
        candidates=candidates,
        history=history,
    )
    by_id = {output.measure_id: output for output in outputs}
    count = 0
    for candidate in candidates:
        if candidate.value is None:
            conn.execute(
                "INSERT INTO measure_history_gaps(measure_id,date,run_id,reason) SELECT ?,?,?,"
                "? WHERE NOT EXISTS (SELECT 1 FROM measure_observations WHERE measure_id=? "
                "AND date=?) ON CONFLICT(measure_id,date) DO UPDATE SET "
                "run_id=excluded.run_id,reason=excluded.reason",
                (
                    candidate.measure_id,
                    candidate.date,
                    run_id,
                    candidate.unavailable_reason,
                    candidate.measure_id,
                    candidate.date,
                ),
            )
            continue
        output = by_id[candidate.measure_id]
        interval = (
            complete_interval(candidate.date, output.period, output.timezone, frozen_at)
            if output.period
            else None
        )
        conn.execute(
            "INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,"
            "definition_revision,period_start_date,period_end_date_exclusive) VALUES(?,?,"
            "?,?,?,?,?,?) ON CONFLICT(measure_id,date) DO UPDATE SET value=excluded.value,"
            "run_id=excluded.run_id,observed_at=excluded.observed_at,"
            "definition_revision=excluded.definition_revision,"
            "period_start_date=excluded.period_start_date,"
            "period_end_date_exclusive=excluded.period_end_date_exclusive",
            (
                candidate.measure_id,
                candidate.date,
                candidate.value,
                run_id,
                frozen_at,
                output.definition_revision,
                interval.start_date if interval else None,
                interval.end_date_exclusive if interval else None,
            ),
        )
        conn.execute(
            "DELETE FROM measure_history_gaps WHERE measure_id=? AND date=?",
            (candidate.measure_id, candidate.date),
        )
        count += 1
    return count
