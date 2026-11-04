"""Dataset definition mutations use the owning source's responsibility."""

import json
import sqlite3
from dataclasses import asdict, replace

from planner.core.contracts import ErrorCode, PlannerError, Principal
from planner.core.ids import new_id
from planner.datasets import data
from planner.datasets.contracts import (
    CreateDatasetBody,
    Dataset,
    DatasetsRead,
    ReadDatasetsBody,
    UpdateDatasetBody,
)
from planner.datasets.logic.validation import validate_definition
from planner.measure_sources import authority as sources_authority
from planner.measure_sources import data as sources
from planner.measures.data import transaction
from planner.measures.logic.dates import invalid


def create_dataset(
    conn: sqlite3.Connection, body: CreateDatasetBody, *, principal: Principal, now: int
) -> Dataset:
    dataset = Dataset(
        new_id("dataset"),
        body["project_id"],
        body["source_id"],
        body["name"],
        body["timezone"],
        body["dimensions"],
        body["fields"],
        1,
        now,
        now,
    )
    validate_definition(dataset)
    with transaction(conn):
        source = sources.read_source(conn, dataset.source_id)
        sources_authority.require_author(conn, principal, source)
        if source.scope.project_id != dataset.project_id:
            raise invalid("dataset and source Projects differ")
        conn.execute(
            "INSERT INTO datasets(id,project_id,source_id,name,timezone,dimensions,fields,"
            "definition_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                dataset.id,
                dataset.project_id,
                dataset.source_id,
                dataset.name,
                dataset.timezone,
                json.dumps([asdict(item) for item in dataset.dimensions]),
                json.dumps([asdict(item) for item in dataset.fields]),
                1,
                now,
                now,
            ),
        )
        sources.bump_source_revision(conn, dataset.source_id, now=now)
    return dataset


def update_dataset(
    conn: sqlite3.Connection,
    dataset_id: str,
    body: UpdateDatasetBody,
    *,
    principal: Principal,
    now: int,
) -> Dataset:
    with transaction(conn):
        current = data.read_dataset(conn, dataset_id)
        sources_authority.require_author(
            conn, principal, sources.read_source(conn, current.source_id)
        )
        if (
            type(body.get("expected_revision")) is not int
            or body["expected_revision"] != current.definition_revision
        ):
            raise PlannerError(ErrorCode.stale_claim, "dataset definition changed")
        edited = replace(
            current,
            name=body.get("name", current.name),
            timezone=body.get("timezone", current.timezone),
            dimensions=body.get("dimensions", current.dimensions),
            fields=body.get("fields", current.fields),
            definition_revision=current.definition_revision + 1,
            updated_at=now,
        )
        validate_definition(edited)
        conn.execute(
            "UPDATE datasets SET name=?,timezone=?,dimensions=?,fields=?,definition_revision=?,"
            "updated_at=? WHERE id=?",
            (
                edited.name,
                edited.timezone,
                json.dumps([asdict(item) for item in edited.dimensions]),
                json.dumps([asdict(item) for item in edited.fields]),
                edited.definition_revision,
                now,
                dataset_id,
            ),
        )
        sources.bump_source_revision(conn, current.source_id, now=now)
    return edited


def read_values(conn: sqlite3.Connection, request: ReadDatasetsBody, *, now: int) -> DatasetsRead:
    return data.read_values(conn, request, now=now)
