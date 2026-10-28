"""Coherent source-data revisions for demanded status reads."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from urllib.parse import parse_qs, urlsplit

from fastapi import Request

from planner.credentials import data as credential_data
from planner.measure_sources import data, views
from planner.measure_sources.settings import DEFAULT_PAGE_LIMIT
from planner.measures import configuration as measure_limits
from planner.measures import data as measure_data
from planner.measures import manual
from planner.measures.logic.dates import local_date
from planner.resource_versions.contracts import ResourceSnapshot, ResourceSourceTrigger
from planner.resource_versions.data import source_revision

_TABLES = (
    "credentials",
    "measure_sources",
    "measures",
    "measure_dependencies",
    "measure_source_runs",
    "measure_observations",
    "measure_history_gaps",
    "measure_source_failure_episodes",
)
SOURCE_TRIGGERS = tuple(
    ResourceSourceTrigger(
        f"resource_measures_{table}_{operation.lower()}",
        table,
        operation,
        "automatic-measures",
        "'all'",
    )
    for table in _TABLES
    for operation in ("INSERT", "UPDATE", "DELETE")
)


class MeasureResources:
    def capture(
        self,
        conn: sqlite3.Connection,
        request: Request,
        key: str,
    ) -> ResourceSnapshot | None:
        parsed = urlsplit(key)
        path = parsed.path
        if not path.startswith(
            (
                "/api/measure-sources",
                "/api/measure-source-runs/",
                "/api/measures",
                "/api/credentials",
            )
        ):
            return None
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            return None
        segments = path.removeprefix("/api/").split("/")
        if len(segments) == 3 and segments[0] == "measures" and segments[2] == "manual-state":
            if set(query) - {"date"}:
                return None
            now = request.app.state.clock.now_unix()
            measure = measure_data.read_measure(conn, segments[1])
            revision = (
                source_revision(conn, "automatic-measures", "all")
                + ":"
                + local_date(now, measure.timezone).isoformat()
            )
            return ResourceSnapshot(
                revision,
                lambda: asdict(
                    manual.read_state(conn, measure.id, query.get("date", [None])[0], now=now)
                ),
            )
        allowed = {"project_id", "source_id", "account", "limit", "offset"}
        if set(query) - allowed:
            return None
        try:
            limit = int(query.get("limit", [str(DEFAULT_PAGE_LIMIT)])[0])
            offset = int(query.get("offset", ["0"])[0])
        except ValueError:
            return None
        views.validate_page(limit, offset)
        project_id = query.get("project_id", [None])[0]
        now = request.app.state.clock.now_unix()
        revision = source_revision(conn, "automatic-measures", "all")
        if path == "/api/measure-sources":
            return ResourceSnapshot(
                revision,
                lambda: views.list_json(
                    conn, project_id=project_id, limit=limit, offset=offset, now=now
                ),
            )
        segments = path.removeprefix("/api/").split("/")
        if segments[0] == "measure-sources" and len(segments) in (2, 3):
            identifier = segments[1]
            if len(segments) == 2:
                return ResourceSnapshot(
                    revision, lambda: asdict(data.read_source(conn, identifier))
                )
            if segments[2] == "status":
                return ResourceSnapshot(
                    revision,
                    lambda: asdict(views.status(conn, identifier, now=now)),
                )
            if segments[2] == "runs":
                return ResourceSnapshot(
                    revision, lambda: views.runs_json(conn, identifier, limit=limit, offset=offset)
                )
        if segments[0] == "measure-source-runs" and len(segments) == 2:
            return ResourceSnapshot(
                revision, lambda: asdict(data.read_run_detail(conn, segments[1]))
            )
        if path == "/api/measures":
            # Match the list API's default exactly.
            count = limit if "limit" in query else measure_limits.DEFAULT_LIST_LIMIT

            def read_measures() -> object:
                page = measure_data.list_measures(
                    conn,
                    project_id=project_id,
                    source_id=query.get("source_id", [None])[0],
                    limit=count,
                    offset=offset,
                )
                return {
                    "measures": [measure_data.measure_json(item) for item in page.rows],
                    "page": page.facts_json(),
                }

            return ResourceSnapshot(revision, read_measures)
        if segments[0] == "measures" and len(segments) == 2:
            return ResourceSnapshot(
                revision,
                lambda: measure_data.measure_json(measure_data.read_measure(conn, segments[1])),
            )
        if path == "/api/credentials":
            count = limit if "limit" in query else measure_limits.DEFAULT_LIST_LIMIT

            def read_credentials() -> object:
                page = credential_data.list_metadata(
                    conn,
                    project_id=project_id,
                    account=query.get("account", [None])[0],
                    limit=count,
                    offset=offset,
                )
                return {
                    "credentials": [credential_data.metadata_json(item) for item in page.rows],
                    "page": page.facts_json(),
                }

            return ResourceSnapshot(revision, read_credentials)
        return None
