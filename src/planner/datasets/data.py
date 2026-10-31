"""Retained dataset definitions, atomic date replacements, and coherent reads."""

import json
import sqlite3
from dataclasses import asdict
from typing import cast

from planner.core.contracts import ErrorCode, PlannerError
from planner.datasets.contracts import (
    CandidateDatasetResult,
    Dataset,
    DatasetCategoryLabel,
    DatasetDate,
    DatasetProvenance,
    DatasetResult,
    DatasetRow,
    DatasetsRead,
    OutputDataset,
    ReadDatasetsBody,
)
from planner.datasets.logic.validation import dimension_key, parse_dimensions, parse_fields
from planner.datasets.logic.validation import validate_batch as validate_results
from planner.list_reads.contracts import ListPage
from planner.measure_sources import data as sources
from planner.measures.data import transaction, validate_page
from planner.measures.logic.dates import date_range, invalid, local_date


def _dataset(row: sqlite3.Row) -> Dataset:
    return Dataset(
        row["id"],
        row["project_id"],
        row["source_id"],
        row["name"],
        row["timezone"],
        parse_dimensions(json.loads(row["dimensions"])),
        parse_fields(json.loads(row["fields"])),
        int(row["definition_revision"]),
        int(row["created_at"]),
        int(row["updated_at"]),
    )


def _output(raw: str) -> OutputDataset:
    output = json.loads(raw)
    return OutputDataset(
        dataset_id=output["dataset_id"],
        definition_revision=output["definition_revision"],
        timezone=output["timezone"],
        dimensions=parse_dimensions(output["dimensions"]),
        fields=parse_fields(output["fields"]),
        required_date=output["required_date"],
    )


def read_dataset(conn: sqlite3.Connection, dataset_id: str) -> Dataset:
    row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "dataset not found")
    return _dataset(row)


def list_datasets(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    source_id: str | None,
    limit: int,
    offset: int,
) -> ListPage[Dataset]:
    validate_page(limit, offset)
    where, args = [], []
    for key, value in (("project_id", project_id), ("source_id", source_id)):
        if value is not None:
            where.append(f"{key}=?")
            args.append(value)
    clause = " WHERE " + " AND ".join(where) if where else ""
    count = int(conn.execute("SELECT COUNT(*) FROM datasets" + clause, args).fetchone()[0])
    rows = conn.execute(
        "SELECT * FROM datasets" + clause + " ORDER BY id LIMIT ? OFFSET ?", (*args, limit, offset)
    )
    return ListPage(tuple(_dataset(row) for row in rows), count, limit, offset)


def prepare_outputs(
    conn: sqlite3.Connection, source_id: str, *, frozen_at: int
) -> tuple[OutputDataset, ...]:
    datasets = (
        _dataset(row)
        for row in conn.execute(
            "SELECT * FROM datasets WHERE source_id=? ORDER BY id", (source_id,)
        )
    )
    return tuple(
        OutputDataset(
            item.id,
            item.definition_revision,
            item.timezone,
            item.dimensions,
            item.fields,
            local_date(frozen_at, item.timezone).isoformat(),
        )
        for item in datasets
    )


def validate_batch(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    outputs: tuple[OutputDataset, ...],
    candidates: tuple[CandidateDatasetResult, ...],
) -> None:
    current_ids = {
        row[0] for row in conn.execute("SELECT id FROM datasets WHERE source_id=?", (source_id,))
    }
    if any(output.dataset_id not in current_ids for output in outputs):
        raise invalid("foreign source dataset output")
    validate_results(outputs, candidates)


def commit_results(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_id: str,
    outputs: tuple[OutputDataset, ...],
    candidates: tuple[CandidateDatasetResult, ...],
    collected_at: int,
) -> int:
    if not conn.in_transaction:
        raise RuntimeError("dataset commit requires caller transaction")
    validate_batch(conn, source_id=source_id, outputs=outputs, candidates=candidates)
    run = conn.execute("SELECT source_id FROM measure_source_runs WHERE id=?", (run_id,)).fetchone()
    if run is None or run[0] != source_id:
        raise invalid("foreign source dataset run")
    by_id = {output.dataset_id: output for output in outputs}
    count = 0
    for result in candidates:
        conn.execute(
            "DELETE FROM dataset_rows WHERE dataset_id=? AND date=?",
            (result.dataset_id, result.date),
        )
        conn.execute(
            "INSERT INTO dataset_results(dataset_id,date,run_id,definition_revision,output,"
            "collected_at,query_end_at,query_start_at,unavailable_reason,limitation) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(dataset_id,date) DO UPDATE SET run_id=excluded.run_id,"
            "definition_revision=excluded.definition_revision,output=excluded.output,"
            "collected_at=excluded.collected_at,"
            "query_end_at=excluded.query_end_at,query_start_at=excluded.query_start_at,"
            "unavailable_reason=excluded.unavailable_reason,limitation=excluded.limitation",
            (
                result.dataset_id,
                result.date,
                run_id,
                by_id[result.dataset_id].definition_revision,
                json.dumps(
                    asdict(by_id[result.dataset_id]), allow_nan=False, separators=(",", ":")
                ),
                collected_at,
                result.query_end_at,
                result.query_start_at,
                result.unavailable_reason,
                result.limitation,
            ),
        )
        conn.executemany(
            "INSERT INTO dataset_rows(dataset_id,date,dimension_key,dimensions,field_values) "
            "VALUES(?,?,?,?,?)",
            [
                (
                    result.dataset_id,
                    result.date,
                    dimension_key(row.dimensions),
                    json.dumps(row.dimensions),
                    json.dumps(row.values),
                )
                for row in result.rows
            ],
        )
        conn.executemany(
            "INSERT INTO dataset_category_labels(dataset_id,dimension,code,label) VALUES(?,?,?,?) "
            "ON CONFLICT(dataset_id,dimension,code) DO UPDATE SET label=excluded.label",
            [
                (result.dataset_id, dimension, code, label)
                for dimension, labels in result.labels.items()
                for code, label in labels.items()
            ],
        )
        count += len(result.rows)
    return count


def earliest_retained_date(conn: sqlite3.Connection, dataset_id: str) -> str | None:
    """Return the first retained result header, including an empty or unavailable result."""
    return cast(
        str | None,
        conn.execute(
            "SELECT MIN(date) FROM dataset_results WHERE dataset_id=?", (dataset_id,)
        ).fetchone()[0],
    )


def read_values(conn: sqlite3.Connection, request: ReadDatasetsBody, *, now: int) -> DatasetsRead:
    identifiers = request["dataset_ids"]
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise invalid("dataset read requires distinct dataset IDs")
    maximum = request.get("limit")
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise invalid("invalid dataset read limit")
    days = date_range(request["first_date"], request["last_date"])
    first, last = request["first_date"], request["last_date"]
    with transaction(conn, read_only=True):
        definitions = tuple(read_dataset(conn, identifier) for identifier in identifiers)
        results: list[DatasetResult] = []
        labels: list[DatasetCategoryLabel] = []
        missing: list[DatasetDate] = []
        returned = matching = 0
        for definition in definitions:
            headers = tuple(
                conn.execute(
                    "SELECT r.*,s.source_id,s.source_revision,s.script_revision,s.script_sha256 "
                    "FROM dataset_results r JOIN measure_source_runs s ON s.id=r.run_id "
                    "WHERE r.dataset_id=? AND r.date>=? AND r.date<=? ORDER BY r.date",
                    (definition.id, first, last),
                )
            )
            present = {header["date"] for header in headers}
            missing.extend(DatasetDate(definition.id, day) for day in days if day not in present)
            for header in headers:
                matching += int(
                    conn.execute(
                        "SELECT COUNT(*) FROM dataset_rows WHERE dataset_id=? AND date=?",
                        (definition.id, header["date"]),
                    ).fetchone()[0]
                )
                remaining = -1 if maximum is None else maximum - returned
                rows = tuple(
                    DatasetRow(json.loads(row[0]), json.loads(row[1]))
                    for row in conn.execute(
                        "SELECT dimensions,field_values FROM dataset_rows "
                        "WHERE dataset_id=? AND date=? "
                        "ORDER BY dimension_key LIMIT ?",
                        (definition.id, header["date"], remaining),
                    )
                )
                returned += len(rows)
                results.append(
                    DatasetResult(
                        definition.id,
                        header["date"],
                        rows,
                        float(header["query_end_at"]),
                        header["query_start_at"],
                        header["unavailable_reason"],
                        header["limitation"],
                        DatasetProvenance(
                            header["run_id"],
                            header["source_id"],
                            int(header["source_revision"]),
                            int(header["script_revision"]),
                            header["script_sha256"],
                            int(header["definition_revision"]),
                            int(header["collected_at"]),
                        ),
                        _output(header["output"]),
                    )
                )
            labels.extend(
                DatasetCategoryLabel(definition.id, row[0], row[1], row[2])
                for row in conn.execute(
                    "SELECT dimension,code,label FROM dataset_category_labels "
                    "WHERE dataset_id=? ORDER BY dimension,code",
                    (definition.id,),
                )
            )
        health = sources.read_health(
            conn, tuple(sorted({item.source_id for item in definitions})), now=now
        )
        return DatasetsRead(
            first,
            last,
            definitions,
            tuple(results),
            tuple(labels),
            tuple(missing),
            health,
            returned,
            matching,
            returned < matching,
        )
