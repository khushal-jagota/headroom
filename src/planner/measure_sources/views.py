"""Explicit bounded source projections; detail reads alone expose source scripts."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.list_reads.contracts import ListPage
from planner.measure_sources import data
from planner.measure_sources.contracts import (
    MeasureSource,
    SourceRunSummary,
    SourceStatus,
    SourceSummary,
)
from planner.measure_sources.records import decode_failure
from planner.measure_sources.settings import LATEST_VALUES_PREVIEW, MAX_PAGE_LIMIT
from planner.measures import data as measures_data
from planner.measures.contracts import LatestMeasureObservation


def source_summary(source: MeasureSource) -> SourceSummary:
    return SourceSummary(
        source.id,
        source.name,
        source.scope,
        source.responsible,
        source.source_revision,
        source.enabled,
        source.local_time,
        None if source.active_script is None else source.active_script.revision,
    )


def _run_summary(row: sqlite3.Row) -> SourceRunSummary:
    from planner.measure_sources.contracts import RunMode, RunStatus

    return SourceRunSummary(
        str(row["id"]),
        RunMode(row["mode"]),
        RunStatus(row["status"]),
        int(row["created_at"]),
        row["finished_at"],
        decode_failure(row["failure"]),
        int(row["observation_count"]),
        int(row["dataset_result_count"]),
        int(row["dataset_row_count"]),
    )


_RUN_SUMMARY_COLUMNS = (
    "id,mode,status,created_at,finished_at,failure,observation_count,"
    "dataset_result_count,dataset_row_count"
)


def status(conn: sqlite3.Connection, source_id: str, *, now: int) -> SourceStatus:
    source = data.read_source(conn, source_id)
    health = data.read_health(conn, (source_id,), now=now)[0]
    latest = conn.execute(
        f"SELECT {_RUN_SUMMARY_COLUMNS} FROM measure_source_runs WHERE source_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    running = conn.execute(
        "SELECT id FROM measure_source_runs WHERE source_id=? AND status='running'",
        (source_id,),
    ).fetchone()
    outputs = measures_data.list_measures(
        conn,
        project_id=None,
        source_id=source_id,
        limit=LATEST_VALUES_PREVIEW,
        offset=0,
    )
    observations = {
        item.measure_id: item
        for item in measures_data.latest_observations(conn, tuple(item.id for item in outputs.rows))
    }
    episode = data.read_failure_episode(conn, source_id)
    failure_row = (
        None
        if episode is None
        else conn.execute(
            "SELECT failure FROM measure_source_runs WHERE id=?",
            (episode.latest_run_id,),
        ).fetchone()
    )
    return SourceStatus(
        source_summary(source),
        health.health,
        None if latest is None else _run_summary(latest),
        health.latest_success_at,
        tuple(
            LatestMeasureObservation(
                item.id,
                item.name,
                item.unit,
                item.kind,
                observations.get(item.id),
            )
            for item in outputs.rows
        ),
        outputs.match_count,
        episode,
        None if failure_row is None else decode_failure(failure_row[0]),
        None if running is None else str(running[0]),
        int(
            conn.execute(
                "SELECT COUNT(*) FROM datasets WHERE source_id=?", (source_id,)
            ).fetchone()[0]
        ),
    )


def validate_page(limit: int, offset: int) -> None:
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_LIMIT
        or type(offset) is not int
        or offset < 0
    ):
        raise PlannerError(ErrorCode.validation, "invalid bounded source list")


def source_ids(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    limit: int,
    offset: int,
) -> ListPage[str]:
    validate_page(limit, offset)
    where = " WHERE project_id=?" if project_id is not None else ""
    args = () if project_id is None else (project_id,)
    count = int(conn.execute("SELECT COUNT(*) FROM measure_sources" + where, args).fetchone()[0])
    rows = conn.execute(
        "SELECT id FROM measure_sources" + where + " ORDER BY id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    )
    return ListPage(tuple(str(row[0]) for row in rows), count, limit, offset)


def list_json(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    limit: int,
    offset: int,
    now: int,
) -> JsonDict:
    page = source_ids(conn, project_id=project_id, limit=limit, offset=offset)
    return {
        "sources": [asdict(status(conn, identifier, now=now)) for identifier in page.rows],
        "page": page.facts_json(),
    }


def runs_json(conn: sqlite3.Connection, source_id: str, *, limit: int, offset: int) -> JsonDict:
    validate_page(limit, offset)
    data.read_source(conn, source_id)
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM measure_source_runs WHERE source_id=?",
            (source_id,),
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT {_RUN_SUMMARY_COLUMNS} FROM measure_source_runs WHERE source_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
        (source_id, limit, offset),
    )
    page = ListPage(tuple(_run_summary(row) for row in rows), count, limit, offset)
    return {"runs": [asdict(row) for row in page.rows], "page": page.facts_json()}
