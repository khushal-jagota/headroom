"""SQLite boundaries absent from pure arithmetic: atomicity, correction, and authority."""

from datetime import UTC, datetime
from sqlite3 import Connection

import pytest

from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.credentials.contracts import CredentialScope
from planner.measure_sources.actions import create_source
from planner.measure_sources.contracts import SourceCreate
from planner.measures import actions, data
from planner.measures.contracts import (
    BinaryExpression,
    CandidateObservation,
    ConstantExpression,
    CountPeriod,
    CreateMeasureBody,
    HistoricalUnavailableReason,
    HistoryWindow,
    Measure,
    MeasureKind,
    MeasureOrigin,
    MeasureReadStatus,
    ReadMeasuresBody,
    ReferenceExpression,
    UpdateMeasureBody,
)

NOW = int(datetime(2026, 10, 27, 12, tzinfo=UTC).timestamp())


def source(conn: Connection) -> str:
    return create_source(
        conn,
        SourceCreate(
            "Synthetic provider", CredentialScope("project_vylo", "fixture"), CHIEF_PRINCIPAL, ()
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    ).id


def taken(
    conn: Connection,
    identifier: str,
    *,
    kind: MeasureKind = MeasureKind.reading,
    period: CountPeriod | None = None,
) -> Measure:
    return actions.create_measure(
        conn,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Synthetic value",
            unit="items",
            origin=MeasureOrigin.taken,
            kind=kind,
            timezone="Europe/Berlin",
            period=period,
            source_id=identifier,
            formula=None,
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )


def run(conn: Connection, source_id: str) -> None:
    conn.execute(
        "INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,source_revision,"
        "script_revision,script_sha256,frozen_at,required_outputs,created_at) "
        "VALUES('run',?,'collection','manual','running',1,1,'synthetic',?,'[]',?)",
        (source_id, NOW, NOW),
    )
    conn.commit()


def collect(
    conn: Connection,
    source_id: str,
    candidates: tuple[CandidateObservation, ...],
    *,
    history: HistoryWindow | None = None,
) -> None:
    with data.transaction(conn):
        data.commit_observations(
            conn,
            run_id="run",
            source_id=source_id,
            frozen_at=NOW,
            outputs=data.prepare_outputs(conn, source_id, frozen_at=NOW),
            candidates=candidates,
            history=history,
        )


def test_atomic_overwrite_history_and_correction(tmp_db: Connection) -> None:
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier)
    run(tmp_db, identifier)
    history = HistoryWindow("2026-10-20", "2026-10-27")
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(measure.id, "2026-10-20", 100),
            CandidateObservation(measure.id, "2026-10-27", 120),
        ),
        history=history,
    )
    current = ReferenceExpression(op="reference", measure_id=measure.id, day_offset=0)
    previous = ReferenceExpression(op="reference", measure_id=measure.id, day_offset=-7)
    growth = actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Growth",
            unit="percent",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="Europe/Berlin",
            period=None,
            source_id=None,
            formula=BinaryExpression(
                op="multiply",
                right=ConstantExpression(op="constant", value=100),
                left=BinaryExpression(
                    op="divide",
                    right=previous,
                    left=BinaryExpression(op="subtract", left=current, right=previous),
                ),
            ),
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    request = ReadMeasuresBody(
        measure_ids=[growth.id], first_date="2026-10-27", last_date="2026-10-27"
    )
    assert actions.read_values(tmp_db, request, now=NOW).values[0].value == 20
    collect(tmp_db, identifier, (CandidateObservation(measure.id, "2026-10-27", 130),))
    assert actions.read_values(tmp_db, request, now=NOW).values[0].value == 30
    actions.update_measure(
        tmp_db,
        growth.id,
        UpdateMeasureBody(
            expected_revision=1,
            formula=BinaryExpression(
                op="multiply", left=current, right=ConstantExpression(op="constant", value=2)
            ),
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    assert actions.read_values(tmp_db, request, now=NOW).values[0].value == 260
    assert [
        (row.date, row.value)
        for row in data.read_observations(tmp_db, measure.id, "2026-10-20", "2026-10-27")
    ] == [("2026-10-20", 100), ("2026-10-27", 130)]
    with pytest.raises(PlannerError, match="foreign"):
        collect(
            tmp_db,
            identifier,
            (
                CandidateObservation(measure.id, "2026-10-27", 999),
                CandidateObservation("foreign", "2026-10-27", 4),
            ),
        )
    assert data.latest_observations(tmp_db, (measure.id,))[0].value == 130
    # Gaps never erase known history; explicit absent history remains inspectable.
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(measure.id, "2026-10-27", 130),
            CandidateObservation(
                measure.id, "2026-10-20", None, HistoricalUnavailableReason.provider_has_no_value
            ),
            CandidateObservation(
                measure.id, "2026-10-21", None, HistoricalUnavailableReason.provider_has_no_value
            ),
        ),
        history=history,
    )
    assert [row[0] for row in tmp_db.execute("SELECT date FROM measure_history_gaps")] == [
        "2026-10-21"
    ]
    assert len(data.read_observations(tmp_db, measure.id, "2026-10-20", "2026-10-27")) == 2


def test_total_neighbors_reject_whole_batch_and_draft_validation_writes_nothing(
    tmp_db: Connection,
) -> None:
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier, kind=MeasureKind.running_total)
    run(tmp_db, identifier)
    history = HistoryWindow("2026-10-25", "2026-10-27")
    collect(
        tmp_db,
        identifier,
        tuple(
            CandidateObservation(measure.id, f"2026-10-{day}", value)
            for day, value in ((25, 10), (26, 13), (27, 18))
        ),
        history=history,
    )
    with pytest.raises(PlannerError, match="decrease"):
        collect(
            tmp_db,
            identifier,
            (
                CandidateObservation(measure.id, "2026-10-26", 19),
                CandidateObservation(measure.id, "2026-10-27", 18),
            ),
            history=history,
        )
    assert [
        row.value for row in data.read_observations(tmp_db, measure.id, "2026-10-25", "2026-10-27")
    ] == [10, 13, 18]
    snapshot = tuple(tuple(row) for row in tmp_db.execute("SELECT * FROM measure_observations"))
    data.validate_batch(
        tmp_db,
        source_id=identifier,
        frozen_at=NOW,
        outputs=data.prepare_outputs(tmp_db, identifier, frozen_at=NOW),
        candidates=(CandidateObservation(measure.id, "2026-10-27", 20),),
        history=None,
    )
    assert (
        tuple(tuple(row) for row in tmp_db.execute("SELECT * FROM measure_observations"))
        == snapshot
    )
    with pytest.raises(PlannerError, match="missing required"):
        collect(tmp_db, identifier, ())
    stale = data.prepare_outputs(tmp_db, identifier, frozen_at=NOW)
    updated = actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(expected_revision=1, name="Corrected name"),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert updated.definition_revision == 2
    with pytest.raises(PlannerError, match="stale"):
        data.validate_batch(
            tmp_db,
            source_id=identifier,
            frozen_at=NOW,
            outputs=stale,
            candidates=(CandidateObservation(measure.id, "2026-10-27", 20),),
            history=None,
        )


def test_count_intervals_definition_authority_and_dependent_units(tmp_db: Connection) -> None:
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier, kind=MeasureKind.period_count, period=CountPeriod.day)
    run(tmp_db, identifier)
    collect(tmp_db, identifier, (CandidateObservation(measure.id, "2026-10-26", 2),))
    interval = data.latest_observations(tmp_db, (measure.id,))[0].period_interval
    assert interval is not None and (interval.start_date, interval.end_date_exclusive) == (
        "2026-10-26",
        "2026-10-27",
    )
    with pytest.raises(PlannerError, match="immutable"):
        actions.update_measure(
            tmp_db,
            measure.id,
            UpdateMeasureBody(expected_revision=1, timezone="UTC"),
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
    derived = actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_other",
            name="Count",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.period_count,
            timezone="Europe/Berlin",
            period=CountPeriod.day,
            source_id=None,
            formula=ReferenceExpression(op="reference", measure_id=measure.id, day_offset=0),
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    with pytest.raises(PlannerError, match="unit edit"):
        actions.update_measure(
            tmp_db,
            measure.id,
            UpdateMeasureBody(expected_revision=1, unit="dollars"),
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
    with pytest.raises(PlannerError):
        actions.update_measure(
            tmp_db,
            derived.id,
            UpdateMeasureBody(expected_revision=1, name="Unauthorized"),
            principal=Principal(PrincipalKind.ticket, "nonexistent"),
            now=NOW,
        )
    result = actions.read_values(
        tmp_db,
        ReadMeasuresBody(measure_ids=[derived.id], first_date="2026-10-26", last_date="2026-10-26"),
        now=NOW,
    )
    assert result.values[0].value == 2 and result.values[0].status is MeasureReadStatus.value
    assert result.sources[0].source_id == identifier
    with pytest.raises(PlannerError, match="output limit"):
        actions.read_values(
            tmp_db,
            ReadMeasuresBody(
                measure_ids=[measure.id, derived.id],
                first_date="2026-01-01",
                last_date="2026-10-26",
            ),
            now=NOW,
        )
    with pytest.raises(PlannerError, match="retained"):
        actions.delete_measure(tmp_db, measure.id, principal=OWNER_PRINCIPAL, now=NOW)
