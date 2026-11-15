"""Bounded SQLite reads and transactional observation intake."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import cast

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
from planner.measures.logic.coverage import observation_coverage, validate_coverage
from planner.measures.logic.dates import (
    calendar_date,
    invalid,
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
        int(row["write_revision"]),
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
            "SELECT * FROM measures WHERE source_id=? ORDER BY id",
            (source_id,),
        )
    )
    return rows


def _observation(
    row: sqlite3.Row, measure: Measure, captured: OutputMeasure | None = None
) -> Observation:
    interval = (
        None
        if row["period_start_date"] is None
        else PeriodInterval(row["period_start_date"], row["period_end_date_exclusive"])
    )
    coverage_status, coverage_reasons = observation_coverage(
        measure,
        captured,
        row["date"],
        row["query_start_at"],
        row["query_end_at"],
        row["limitation"],
    )
    return Observation(
        row["measure_id"],
        row["date"],
        float(row["value"]),
        row["run_id"],
        int(row["observed_at"]),
        int(row["definition_revision"]),
        interval,
        row["manual_write_id"],
        row["query_start_at"],
        row["query_end_at"],
        row["limitation"],
        coverage_status,
        coverage_reasons,
    )


def _hydrate_observations(
    conn: sqlite3.Connection,
    rows: tuple[sqlite3.Row, ...],
    definitions: dict[str, Measure] | None = None,
) -> tuple[Observation, ...]:
    # The receipt is the captured definition. Load it once per batch, not per date.
    from planner.measure_sources.records import decode_outputs

    current = dict(definitions or {})
    for identifier in {str(row["measure_id"]) for row in rows} - current.keys():
        current[identifier] = read_measure(conn, identifier)
    run_ids = sorted({str(row["run_id"]) for row in rows if row["run_id"] is not None})
    captured: dict[tuple[str, str], OutputMeasure] = {}
    for start in range(0, len(run_ids), limits.SQL_IN_CHUNK):
        chunk = run_ids[start : start + limits.SQL_IN_CHUNK]
        marks = ",".join("?" for _ in chunk)
        for run in conn.execute(
            f"SELECT id,required_outputs FROM measure_source_runs WHERE id IN ({marks})", chunk
        ):
            for output in decode_outputs(run["required_outputs"]):
                captured[(run["id"], output.measure_id)] = output
    return tuple(
        _observation(
            row, current[row["measure_id"]], captured.get((row["run_id"], row["measure_id"]))
        )
        for row in rows
    )


def latest_observations(
    conn: sqlite3.Connection, measure_ids: tuple[str, ...]
) -> tuple[Observation, ...]:
    result = []
    for identifier in measure_ids:
        row = conn.execute(
            "SELECT * FROM measure_observations WHERE measure_id=? ORDER BY date DESC LIMIT 1",
            (identifier,),
        ).fetchone()
        if row is not None:
            result.append(row)
    return _hydrate_observations(conn, tuple(result))


def read_observations(
    conn: sqlite3.Connection, measure_id: str, first_date: str, last_date: str
) -> tuple[Observation, ...]:
    if calendar_date(first_date) > calendar_date(last_date):
        raise invalid("measure date range is reversed")
    rows = conn.execute(
        "SELECT * FROM measure_observations WHERE measure_id=? AND date>=? AND "
        "date<=? ORDER BY date",
        (measure_id, first_date, last_date),
    )
    return _hydrate_observations(conn, tuple(rows))


def earliest_retained_date(conn: sqlite3.Connection, measure_id: str) -> str | None:
    """Return the first retained observation or gap across a measure and its inputs."""
    return cast(
        str | None,
        conn.execute(
            "WITH RECURSIVE inputs(id) AS (SELECT ? UNION "
            "SELECT input_measure_id FROM measure_dependencies "
            "JOIN inputs ON measure_id=id) "
            "SELECT MIN(date) FROM (SELECT date FROM measure_observations "
            "WHERE measure_id IN inputs "
            "UNION ALL SELECT date FROM measure_history_gaps WHERE measure_id IN inputs)",
            (measure_id,),
        ).fetchone()[0],
    )


def read_recorded_dates(
    conn: sqlite3.Connection, measure_id: str, first_date: str, last_date: str
) -> frozenset[str]:
    """Return dates recorded for this measure itself, including explicit history gaps."""
    return frozenset(
        row[0]
        for row in conn.execute(
            "SELECT date FROM measure_observations WHERE measure_id=? AND date>=? AND date<=? "
            "UNION SELECT date FROM measure_history_gaps "
            "WHERE measure_id=? AND date>=? AND date<=?",
            (measure_id, first_date, last_date, measure_id, first_date, last_date),
        )
    )


def read_demanded(
    conn: sqlite3.Connection, pairs: set[MeasureDate], definitions: dict[str, Measure]
) -> dict[MeasureDate, Observation]:
    selected: list[sqlite3.Row] = []
    for identifier, measure in definitions.items():
        if measure.origin is MeasureOrigin.assembled:
            continue
        days = sorted(pair.date for pair in pairs if pair.measure_id == identifier)
        for start in range(0, len(days), limits.SQL_IN_CHUNK):
            chunk = days[start : start + limits.SQL_IN_CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT * FROM measure_observations WHERE measure_id=? AND date IN ({marks})",
                (identifier, *chunk),
            )
            selected.extend(rows)
    return {
        MeasureDate(item.measure_id, item.date): item
        for item in _hydrate_observations(conn, tuple(selected), definitions)
    }


def read_definitions(
    conn: sqlite3.Connection,
    roots: tuple[str, ...],
    *,
    proposed: Measure | None = None,
    missing_ok: bool = False,
) -> dict[str, Measure]:
    result: dict[str, Measure] = {}
    visited: set[str] = set()
    pending = list(roots)
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        try:
            current = (
                proposed
                if proposed is not None and identifier == proposed.id
                else read_measure(conn, identifier)
            )
        except PlannerError as error:
            if missing_ok and error.code is ErrorCode.not_found:
                continue
            raise
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
        day = local_date(frozen_at, measure.timezone).isoformat()
        outputs.append(
            OutputMeasure(
                measure.id,
                measure.definition_revision,
                measure.kind,
                measure.timezone,
                period,
                day,
                None,
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
    by_id = {out.measure_id: out for out in outputs}
    current_ids = {measure.id for measure in read_source_outputs(conn, source_id)}
    seen: set[MeasureDate] = set()
    for candidate in candidates:
        pair = MeasureDate(candidate.measure_id, candidate.date)
        if (
            pair in seen
            or candidate.measure_id not in by_id
            or candidate.measure_id not in current_ids
        ):
            raise invalid("duplicate or foreign source output")
        seen.add(pair)
        calendar_date(candidate.date)
        validate_coverage(candidate.query_start_at, candidate.query_end_at, candidate.limitation)
        if candidate.value is None:
            if not isinstance(candidate.unavailable_reason, HistoricalUnavailableReason):
                raise invalid("missing output requires a reason")
        else:
            if candidate.unavailable_reason is not None:
                raise invalid("value cannot carry an unavailable reason")
            number(candidate.value)


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
                "DELETE FROM measure_observations WHERE measure_id=? AND date=?",
                (candidate.measure_id, candidate.date),
            )
            conn.execute(
                "INSERT INTO measure_history_gaps(measure_id,date,run_id,reason) VALUES(?,?,?,?) "
                "ON CONFLICT(measure_id,date) DO UPDATE SET "
                "run_id=excluded.run_id,reason=excluded.reason",
                (
                    candidate.measure_id,
                    candidate.date,
                    run_id,
                    candidate.unavailable_reason,
                ),
            )
            continue
        output = by_id[candidate.measure_id]
        conn.execute(
            "INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,"
            "definition_revision,period_start_date,period_end_date_exclusive,query_start_at,"
            "query_end_at,limitation) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(measure_id,date) DO UPDATE SET value=excluded.value,"
            "run_id=excluded.run_id,manual_write_id=NULL,observed_at=excluded.observed_at,"
            "definition_revision=excluded.definition_revision,"
            "period_start_date=excluded.period_start_date,"
            "period_end_date_exclusive=excluded.period_end_date_exclusive,"
            "query_start_at=excluded.query_start_at,query_end_at=excluded.query_end_at,"
            "limitation=excluded.limitation",
            (
                candidate.measure_id,
                candidate.date,
                candidate.value,
                run_id,
                frozen_at,
                output.definition_revision,
                None,
                None,
                candidate.query_start_at,
                candidate.query_end_at,
                candidate.limitation,
            ),
        )
        conn.execute(
            "DELETE FROM measure_history_gaps WHERE measure_id=? AND date=?",
            (candidate.measure_id, candidate.date),
        )
        count += 1
    return count
