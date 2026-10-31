"""SQLite boundaries absent from pure arithmetic: atomicity, correction, and authority."""

from datetime import UTC, datetime
from sqlite3 import Connection

import pytest
from tests.unit.test_manager_wakes import _item_and_ticket

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.measure_sources.actions import create_source
from planner.measure_sources.contracts import SourceCreate, SourceScope
from planner.measure_sources.records import decode_candidates, encode_outputs
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
from planner.measures.contracts import (
    ScalarCoverageStatus as Coverage,
)

NOW = int(datetime(2026, 10, 27, 12, tzinfo=UTC).timestamp())


def source(conn: Connection, *, responsible: Principal = CHIEF_PRINCIPAL) -> str:
    return create_source(
        conn,
        SourceCreate("Synthetic provider", SourceScope("project_vylo", "fixture"), responsible),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    ).id


def taken(
    conn: Connection,
    identifier: str,
    *,
    kind: MeasureKind = MeasureKind.reading,
    period: CountPeriod | None = None,
    principal: Principal = CHIEF_PRINCIPAL,
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
        principal=principal,
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
    # A restated unavailable result replaces an earlier value, just like a new value.
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
    assert {row[0] for row in tmp_db.execute("SELECT date FROM measure_history_gaps")} == {
        "2026-10-20",
        "2026-10-21",
    }
    assert len(data.read_observations(tmp_db, measure.id, "2026-10-20", "2026-10-27")) == 1
    collect(tmp_db, identifier, (CandidateObservation(measure.id, "2026-10-20", 99),))
    assert {row[0] for row in tmp_db.execute("SELECT date FROM measure_history_gaps")} == {
        "2026-10-21"
    }


def test_restated_numbers_are_accepted_and_draft_validation_writes_nothing(
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
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(measure.id, "2026-10-26", -19.5),
            CandidateObservation(measure.id, "2024-01-01", 5),
        ),
        history=history,
    )
    assert [
        row.value for row in data.read_observations(tmp_db, measure.id, "2024-01-01", "2026-10-27")
    ] == [5, 10, -19.5, 18]
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
    collect(tmp_db, identifier, ())
    assert (
        tuple(tuple(row) for row in tmp_db.execute("SELECT * FROM measure_observations"))
        == snapshot
    )
    stale = data.prepare_outputs(tmp_db, identifier, frozen_at=NOW)
    updated = actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(expected_revision=1, name="Corrected name"),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert updated.definition_revision == 2
    # Metadata corrections do not invalidate real in-flight values; captured revision is provenance.
    with data.transaction(tmp_db):
        data.commit_observations(
            tmp_db,
            run_id="run",
            source_id=identifier,
            frozen_at=NOW,
            outputs=stale,
            candidates=(CandidateObservation(measure.id, "2026-10-27", 20),),
            history=None,
        )
    observation = data.latest_observations(tmp_db, (measure.id,))[0]
    assert observation.value == 20 and observation.definition_revision == 1


def test_metadata_edits_and_large_history_reads_do_not_police_numbers(tmp_db: Connection) -> None:
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier, kind=MeasureKind.period_count, period=CountPeriod.day)
    run(tmp_db, identifier)
    collect(tmp_db, identifier, (CandidateObservation(measure.id, "2026-10-27", -2.5),))
    assert data.latest_observations(tmp_db, (measure.id,))[0].period_interval is None
    edited = actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(
            expected_revision=1, timezone="UTC", unit="dollars", kind=MeasureKind.running_total
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert edited.timezone == "UTC" and edited.unit == "dollars"
    derived = actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_other",
            name="Count",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.period_count,
            timezone="Europe/Berlin",
            period=CountPeriod.week,
            source_id=None,
            formula=ReferenceExpression(op="reference", measure_id=measure.id, day_offset=0),
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    result = actions.read_values(
        tmp_db,
        ReadMeasuresBody(
            measure_ids=[measure.id, derived.id], first_date="2025-01-01", last_date="2026-10-27"
        ),
        now=NOW,
    )
    assert len(result.values) > 366
    page = actions.read_values(
        tmp_db,
        ReadMeasuresBody(
            measure_ids=[measure.id], first_date="2025-01-01", last_date="2026-10-27", limit=2
        ),
        now=NOW,
    )
    assert [value.date for value in page.values] == ["2025-01-01", "2025-01-02"]
    assert result.values[-1].value == -2.5 and result.values[-1].status is MeasureReadStatus.value
    assert result.sources[0].source_id == identifier
    with pytest.raises(PlannerError, match="retained"):
        actions.delete_measure(tmp_db, measure.id, principal=OWNER_PRINCIPAL, now=NOW)


def test_scalar_coverage_round_trip_correction_and_captured_definition(tmp_db: Connection) -> None:
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier, kind=MeasureKind.period_count, period=CountPeriod.day)
    run(tmp_db, identifier)
    tmp_db.execute(
        "UPDATE measure_source_runs SET required_outputs=? WHERE id='run'",
        (encode_outputs(data.prepare_outputs(tmp_db, identifier, frozen_at=NOW)),),
    )
    tmp_db.commit()
    # The captured Berlin day begins at 23:00 UTC. The second observation is clipped.
    start = datetime(2026, 10, 26, 23, tzinfo=UTC).timestamp()
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(
                measure.id, "2026-10-27", 0, query_start_at=start, query_end_at=start + 86400
            ),
            CandidateObservation(
                measure.id,
                "2026-10-28",
                -2.5,
                query_start_at=start + 86401,
                query_end_at=start + 172800,
                limitation="First second omitted",
            ),
        ),
    )
    queries: list[str] = []
    tmp_db.set_trace_callback(queries.append)
    observations = data.read_observations(tmp_db, measure.id, "2026-10-27", "2026-10-28")
    tmp_db.set_trace_callback(None)
    assert (
        len(
            [
                query
                for query in queries
                if "SELECT id,required_outputs FROM measure_source_runs" in query
            ]
        )
        == 1
    )
    assert observations[0].value == 0 and observations[0].coverage_status is Coverage.complete
    assert observations[1].value == -2.5 and observations[1].coverage_status is Coverage.partial
    assert observations[1].limitation == "First second omitted"
    request = ReadMeasuresBody(
        measure_ids=[measure.id], first_date="2026-10-27", last_date="2026-10-27"
    )
    value = actions.read_values(tmp_db, request, now=NOW).values[0]
    assert (value.query_start_at, value.query_end_at, value.coverage_status) == (
        start,
        start + 86400,
        Coverage.complete,
    )
    actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(expected_revision=1, name="Renamed"),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert (
        actions.read_values(tmp_db, request, now=NOW).values[0].coverage_status is Coverage.complete
    )
    actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(expected_revision=2, period=CountPeriod.week),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert (
        actions.read_values(tmp_db, request, now=NOW).values[0].coverage_status is Coverage.unknown
    )
    assert data.latest_observations(tmp_db, (measure.id,))[0].coverage_status is Coverage.partial
    # Corrections without facts cannot inherit facts from the old number.
    collect(tmp_db, identifier, (CandidateObservation(measure.id, "2026-10-27", 3.5),))
    corrected = actions.read_values(tmp_db, request, now=NOW).values[0]
    assert corrected.value == 3.5
    assert corrected.query_start_at is None and corrected.query_end_at is None
    assert corrected.limitation is None
    assert corrected.coverage_status is Coverage.unknown
    assert data.latest_observations(tmp_db, (measure.id,))[0].value == -2.5
    legacy = decode_candidates(
        '[{"measure_id":"legacy","date":"2026-10-27","value":0,"unavailable_reason":null}]'
    )
    assert legacy == (CandidateObservation("legacy", "2026-10-27", 0),)


def test_assigned_worker_can_manage_taken_definitions(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    _, other_ticket_id = _item_and_ticket(tmp_db, fake_clock)
    worker = Principal(PrincipalKind.ticket, ticket_id)
    outsider = Principal(PrincipalKind.ticket, other_ticket_id)
    identifier = source(tmp_db, responsible=Principal(PrincipalKind.manager, manager_id))
    measure = taken(tmp_db, identifier, principal=worker)
    with pytest.raises(PlannerError):
        taken(tmp_db, identifier, principal=outsider)
    with pytest.raises(PlannerError):
        actions.update_measure(
            tmp_db,
            measure.id,
            UpdateMeasureBody(expected_revision=1, name="Other"),
            principal=outsider,
            now=NOW,
        )
    changed = actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(expected_revision=1, name="Repaired"),
        principal=worker,
        now=NOW,
    )
    assert changed.name == "Repaired"
    with pytest.raises(PlannerError):
        actions.delete_measure(tmp_db, measure.id, principal=outsider, now=NOW)
    actions.delete_measure(tmp_db, measure.id, principal=worker, now=NOW)
    assert tmp_db.execute("SELECT 1 FROM measures WHERE id=?", (measure.id,)).fetchone() is None
    # Goal reads tolerate deleted formula inputs, while save/read defaults remain strict.
    assert data.read_definitions(tmp_db, (measure.id,), missing_ok=True) == {}
    with pytest.raises(PlannerError, match="measure not found"):
        data.read_definitions(tmp_db, (measure.id,))


def test_retained_history_follows_dependencies_but_recorded_dates_stay_on_the_root(
    tmp_db: Connection,
) -> None:
    identifier = source(tmp_db)
    leaf, unrelated = taken(tmp_db, identifier), taken(tmp_db, identifier)
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(
                leaf.id, "2026-10-20", None, HistoricalUnavailableReason.provider_has_no_value
            ),
            CandidateObservation(leaf.id, "2026-10-25", 2),
            CandidateObservation(leaf.id, "2026-10-28", 3),
            CandidateObservation(unrelated.id, "2026-10-10", 99),
        ),
        history=HistoryWindow("2026-10-10", "2026-10-28"),
    )
    root = leaf
    for name in ("Intermediate", "Root"):
        root = actions.create_measure(
            tmp_db,
            CreateMeasureBody(
                project_id="project_vylo",
                name=name,
                unit="items",
                origin=MeasureOrigin.assembled,
                kind=MeasureKind.reading,
                timezone="Europe/Berlin",
                period=None,
                source_id=None,
                formula=ReferenceExpression(op="reference", measure_id=root.id, day_offset=0),
            ),
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
    with data.transaction(tmp_db, read_only=True):
        assert data.earliest_retained_date(tmp_db, root.id) == "2026-10-20"
        assert data.read_recorded_dates(tmp_db, leaf.id, "2026-10-20", "2026-10-28") == {
            "2026-10-20",
            "2026-10-25",
            "2026-10-28",
        }
        assert data.read_recorded_dates(tmp_db, root.id, "2026-10-20", "2026-10-28") == frozenset()
        assert tmp_db.in_transaction
