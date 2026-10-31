"""Canonical measure mutations and coherent bounded computation."""

import json
import sqlite3
from dataclasses import replace
from itertools import islice

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal
from planner.core.ids import new_id
from planner.measures import configuration as limits
from planner.measures import data
from planner.measures.contracts import (
    CreateMeasureBody,
    Measure,
    MeasureDate,
    MeasureReadStatus,
    MeasuresRead,
    MeasureValue,
    ReadMeasuresBody,
    UpdateMeasureBody,
)
from planner.measures.logic.dates import date_range, invalid
from planner.measures.logic.evaluation import demanded_pairs, evaluate
from planner.measures.logic.formulas import dependencies, parse_formula, validate_graph


def _require_author(conn: sqlite3.Connection, principal: Principal, source_id: str | None) -> None:
    if source_id is None:
        authority.require_above(conn, principal, authority.owner_only("measures"))
    else:
        from planner.measure_sources import authority as sources_authority
        from planner.measure_sources import data as sources_data

        sources_authority.require_author(conn, principal, sources_data.read_source(conn, source_id))


def _validate_text(measure: Measure) -> None:
    if (
        not isinstance(measure.name, str)
        or not measure.name.strip()
        or len(measure.name) > limits.MAX_NAME_LENGTH
    ):
        raise invalid("invalid measure name")
    if not isinstance(measure.unit, str) or len(measure.unit) > limits.MAX_UNIT_LENGTH:
        raise invalid("invalid measure unit")


def _validate_source(conn: sqlite3.Connection, measure: Measure) -> None:
    if measure.source_id is None:
        return
    from planner.measure_sources import data as sources

    source = sources.read_source(conn, measure.source_id)
    if source.scope.project_id != measure.project_id:
        raise invalid("measure and source Projects differ")


def _bump_source(conn: sqlite3.Connection, measure: Measure, now: int) -> None:
    if measure.source_id is not None:
        from planner.measure_sources import data as sources

        sources.bump_source_revision(conn, measure.source_id, now=now)


def _write_edges(conn: sqlite3.Connection, measure: Measure) -> None:
    conn.execute("DELETE FROM measure_dependencies WHERE measure_id=?", (measure.id,))
    if measure.formula is not None:
        conn.executemany(
            "INSERT INTO measure_dependencies(measure_id,input_measure_id) VALUES(?,?)",
            [(measure.id, child) for child in sorted(dependencies(measure.formula))],
        )


def create_measure(
    conn: sqlite3.Connection, body: CreateMeasureBody, *, principal: Principal, now: int
) -> Measure:
    formula = parse_formula(body["formula"]) if body["formula"] is not None else None
    measure = Measure(
        new_id("measure"),
        body["project_id"],
        body["name"],
        body["unit"],
        body["origin"],
        body["kind"],
        body["timezone"],
        body["period"],
        body["source_id"],
        formula,
        1,
        now,
        now,
    )
    _validate_text(measure)
    with data.transaction(conn):
        _require_author(conn, principal, body["source_id"])
        if (
            conn.execute("SELECT 1 FROM projects WHERE id=?", (measure.project_id,)).fetchone()
            is None
        ):
            raise invalid("measure Project does not exist")
        definitions = data.read_definitions(conn, (measure.id,), proposed=measure)
        validate_graph(measure.id, definitions)
        _validate_source(conn, measure)
        conn.execute(
            "INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,period,"
            "source_id,formula,definition_revision,created_at,updated_at) VALUES(?,?,?,?,"
            "?,?,?,?,?,?,?,?,?)",
            (
                measure.id,
                measure.project_id,
                measure.name,
                measure.unit,
                measure.origin,
                measure.kind,
                measure.timezone,
                measure.period,
                measure.source_id,
                json.dumps(measure.formula) if formula is not None else None,
                1,
                now,
                now,
            ),
        )
        _write_edges(conn, measure)
        _bump_source(conn, measure, now)
    return measure


def update_measure(
    conn: sqlite3.Connection,
    measure_id: str,
    body: UpdateMeasureBody,
    *,
    principal: Principal,
    now: int,
) -> Measure:
    with data.transaction(conn):
        current = data.read_measure(conn, measure_id)
        _require_author(conn, principal, current.source_id)
        if (
            type(body.get("expected_revision")) is not int
            or body["expected_revision"] != current.definition_revision
        ):
            raise PlannerError(ErrorCode.stale_claim, "measure definition changed")
        edited = replace(
            current,
            name=body.get("name", current.name),
            unit=body.get("unit", current.unit),
            kind=body.get("kind", current.kind),
            timezone=body.get("timezone", current.timezone),
            period=body.get("period", current.period),
            formula=parse_formula(body["formula"]) if "formula" in body else current.formula,
            definition_revision=current.definition_revision + 1,
            updated_at=now,
        )
        _validate_text(edited)
        definitions = data.read_definitions(conn, (measure_id,), proposed=edited)
        validate_graph(measure_id, definitions)
        conn.execute(
            "UPDATE measures SET name=?,unit=?,kind=?,timezone=?,period=?,formula=?,"
            "definition_revision=?,updated_at=? WHERE id=?",
            (
                edited.name,
                edited.unit,
                edited.kind,
                edited.timezone,
                edited.period,
                json.dumps(edited.formula) if edited.formula is not None else None,
                edited.definition_revision,
                now,
                measure_id,
            ),
        )
        _write_edges(conn, edited)
        _bump_source(conn, edited, now)
        return edited


def delete_measure(
    conn: sqlite3.Connection, measure_id: str, *, principal: Principal, now: int
) -> None:
    with data.transaction(conn):
        measure = data.read_measure(conn, measure_id)
        _require_author(conn, principal, measure.source_id)
        retained = conn.execute(
            "SELECT 1 FROM measure_dependencies WHERE input_measure_id=? UNION ALL SELECT "
            "1 FROM measure_observations WHERE measure_id=? UNION ALL SELECT 1 FROM "
            "measure_history_gaps WHERE measure_id=? LIMIT 1",
            (measure_id, measure_id, measure_id),
        ).fetchone()
        if retained is not None:
            raise invalid("referenced or retained measure cannot be deleted")
        conn.execute("DELETE FROM measures WHERE id=?", (measure_id,))
        _bump_source(conn, measure, now)


def read_values(conn: sqlite3.Connection, request: ReadMeasuresBody, *, now: int) -> MeasuresRead:
    identifiers = request["measure_ids"]
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise invalid("measure read requires distinct measure IDs")
    maximum = request.get("limit")
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise invalid("invalid measure read limit")
    days = date_range(request["first_date"], request["last_date"])
    pairs_to_read = (MeasureDate(identifier, day) for identifier in identifiers for day in days)
    roots = tuple(islice(pairs_to_read, maximum)) if maximum is not None else tuple(pairs_to_read)
    with data.transaction(conn, read_only=True):
        definitions = data.read_definitions(conn, tuple(identifiers))
        for identifier in identifiers:
            validate_graph(identifier, definitions)
        try:
            pairs = demanded_pairs(roots, definitions, now=now)
            observations = data.read_demanded(conn, pairs, definitions)
            values = evaluate(roots, definitions, observations, now=now)
        except RecursionError:
            values = tuple(
                MeasureValue(
                    pair.measure_id,
                    pair.date,
                    definitions[pair.measure_id].definition_revision,
                    MeasureReadStatus.arithmetic_limit,
                    None,
                    (),
                    (),
                )
                for pair in roots
            )
        from planner.measure_sources import data as sources

        source_ids = tuple(
            sorted(
                {
                    measure.source_id
                    for measure in definitions.values()
                    if measure.source_id is not None
                }
            )
        )
        health = sources.read_health(conn, source_ids, now=now)
        return MeasuresRead(values, health)
