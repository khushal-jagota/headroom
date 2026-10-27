"""Small canonical source setup shared by source integration checks."""

from datetime import UTC, datetime
from sqlite3 import Connection

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal
from planner.credentials.contracts import CredentialScope
from planner.measure_sources import actions, data
from planner.measure_sources.contracts import (
    MeasureSource,
    SourceCreate,
    SourceScheduleBody,
    SourceScriptBody,
)
from planner.measures import actions as measures
from planner.measures.contracts import CreateMeasureBody, Measure, MeasureKind, MeasureOrigin

NOW = int(datetime(2026, 10, 27, 12, tzinfo=UTC).timestamp())


def configured_source(
    conn: Connection,
    *,
    responsible: Principal = CHIEF_PRINCIPAL,
    now: int = NOW,
    local_time: str = "12:00",
    enabled: bool = True,
    principal: Principal = OWNER_PRINCIPAL,
) -> tuple[MeasureSource, Measure]:
    source = actions.create_source(
        conn,
        SourceCreate("Synthetic source", CredentialScope("project_vylo", "fixture"), responsible),
        principal=principal,
        now=now,
    )
    measure = measures.create_measure(
        conn,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Synthetic reading",
            unit="items",
            origin=MeasureOrigin.taken,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=source.id,
            formula=None,
        ),
        principal=principal,
        now=now,
    )
    source = data.read_source(conn, source.id)
    source = actions.submit_script(
        conn,
        source.id,
        SourceScriptBody(expected_revision=source.source_revision, text="pass"),
        principal=responsible,
        now=now,
    )
    if enabled:
        source = actions.set_schedule(
            conn,
            source.id,
            SourceScheduleBody(
                expected_revision=source.source_revision, enabled=True, local_time=local_time
            ),
            principal=OWNER_PRINCIPAL,
            now=now,
        )
    return source, measure
