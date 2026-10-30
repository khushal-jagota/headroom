"""Manual writes add concurrency and replay risks absent from provider collection."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from sqlite3 import Connection
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from tests.unit.test_measure_persistence import NOW, source, taken
from tests.unit.test_scheduled_ticket_authority import _app

from planner.core import db
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.measures import actions, data, manual
from planner.measures.contracts import (
    CountPeriod,
    CreateMeasureBody,
    ManualIncrementBody,
    ManualReplaceBody,
    Measure,
    MeasureKind,
    MeasureOrigin,
    ReadMeasuresBody,
    ReferenceExpression,
    ScalarCoverageStatus,
    UpdateMeasureBody,
)


def manual_measure(conn: Connection, *, zone: str = "Europe/Berlin") -> Measure:
    return actions.create_measure(
        conn,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Manual sample",
            unit="items",
            origin=MeasureOrigin.manual,
            kind=MeasureKind.reading,
            timezone=zone,
            period=None,
            source_id=None,
            formula=None,
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )


def replacement(request_id: str, day: str, value: float, revision: int) -> ManualReplaceBody:
    return ManualReplaceBody(
        request_id=request_id,
        date=day,
        expected_definition_revision=1,
        basis="date",
        operation="replace",
        value=value,
        expected_write_revision=revision,
    )


def test_manual_replay_stale_edits_and_shared_formula_provenance(tmp_db: Connection) -> None:
    measure = manual_measure(tmp_db)
    absent = manual.read_state(tmp_db, measure.id, "2026-10-26", now=NOW)
    assert absent.observation is None and absent.write_revision == 0
    first = replacement("first", "2026-10-26", -1.25, 0)
    accepted = manual.write_value(tmp_db, measure.id, first, principal=OWNER_PRINCIPAL, now=NOW)
    second = replacement("second", "2026-10-27", 2.5, 1)
    manual.write_value(tmp_db, measure.id, second, principal=OWNER_PRINCIPAL, now=NOW)
    assert manual.read_state(tmp_db, measure.id, "2026-10-25", now=NOW).write_revision == 2
    with pytest.raises(PlannerError, match="value changed") as conflict:
        manual.write_value(
            tmp_db,
            measure.id,
            replacement("stale", "2026-10-25", 8, 0),
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
    assert conflict.value.detail["state"]["write_revision"] == 2
    assert conflict.value.detail["state"]["observation"] is None
    actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(expected_revision=1, name="Edited"),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    replay = manual.write_value(tmp_db, measure.id, first, principal=OWNER_PRINCIPAL, now=NOW + 1)
    assert replay.replayed and replay.receipt == accepted.receipt
    assert replay.state.definition_revision == 2 and replay.state.write_revision == 2
    for body, actor in (({**first, "value": 99}, OWNER_PRINCIPAL), (first, CHIEF_PRINCIPAL)):
        with pytest.raises(PlannerError, match="already used"):
            manual.write_value(tmp_db, measure.id, body, principal=actor, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(PlannerError, match="definition changed"):
        manual.write_value(
            tmp_db,
            measure.id,
            replacement("old-definition", "2026-10-26", 7, 2),
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
    derived = actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_other",
            name="Derived manual",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula=ReferenceExpression(op="reference", measure_id=measure.id, day_offset=0),
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    result = actions.read_values(
        tmp_db,
        ReadMeasuresBody(measure_ids=[derived.id], first_date="2026-10-26", last_date="2026-10-27"),
        now=NOW,
    )
    assert [value.value for value in result.values] == [-1.25, 2.5]
    assert result.values[0].inputs[0].manual_write_id == "first"
    assert result.values[0].inputs[0].run_id is None and result.sources == ()
    assert tmp_db.execute("SELECT COUNT(*) FROM manual_measure_writes").fetchone()[0] == 2


def test_manual_period_count_keeps_value_unknown_and_clears_prior_coverage(
    tmp_db: Connection,
) -> None:
    measure = manual_measure(tmp_db)
    measure = actions.update_measure(
        tmp_db,
        measure.id,
        UpdateMeasureBody(
            expected_revision=1, kind=MeasureKind.period_count, period=CountPeriod.day
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    first = replacement("count-first", "2026-10-27", 0, 0)
    first["expected_definition_revision"] = measure.definition_revision
    accepted = manual.write_value(tmp_db, measure.id, first, principal=OWNER_PRINCIPAL, now=NOW)
    observed = accepted.state.observation
    assert observed is not None and observed.value == 0
    assert observed.coverage_status is ScalarCoverageStatus.unknown
    # An old populated row must not lend source facts to a new manual replacement.
    tmp_db.execute(
        "UPDATE measure_observations SET query_start_at=1,query_end_at=2,limitation='Old' "
        "WHERE measure_id=?",
        (measure.id,),
    )
    tmp_db.commit()
    second = replacement("count-second", "2026-10-27", -2.5, 1)
    second["expected_definition_revision"] = measure.definition_revision
    result = manual.write_value(tmp_db, measure.id, second, principal=OWNER_PRINCIPAL, now=NOW)
    observed = result.state.observation
    assert observed is not None and observed.value == -2.5
    assert observed.query_start_at is None and observed.query_end_at is None
    assert observed.limitation is None
    assert observed.coverage_status is ScalarCoverageStatus.unknown
    replay = manual.write_value(tmp_db, measure.id, second, principal=OWNER_PRINCIPAL, now=NOW)
    assert replay.replayed and replay.receipt == result.receipt


def test_manual_atomic_increments_latest_snapshots_and_date_isolation(tmp_db: Connection) -> None:
    measure = manual_measure(tmp_db)
    barrier = Barrier(2)
    database = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    def increment(worker: int) -> list[float]:
        conn = db.connect(database)
        try:
            barrier.wait(timeout=10)
            return [
                manual.write_value(
                    conn,
                    measure.id,
                    ManualIncrementBody(
                        request_id=f"parallel-{worker}-{index}",
                        date="2026-10-26",
                        expected_definition_revision=1,
                        basis="date",
                        operation="increment",
                        delta=0.25,
                    ),
                    principal=OWNER_PRINCIPAL,
                    now=NOW,
                ).receipt.value
                for index in range(4)
            ]
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(increment, (1, 2)))
    assert sorted(value for group in values for value in group) == [0.25 * n for n in range(1, 9)]
    request = ManualIncrementBody(
        request_id="latest",
        date="2026-10-27",
        expected_definition_revision=1,
        basis="latest",
        operation="increment",
        delta=-0.5,
    )
    accepted = manual.write_value(tmp_db, measure.id, request, principal=OWNER_PRINCIPAL, now=NOW)
    assert accepted.receipt.value == 1.5
    manual.write_value(
        tmp_db,
        measure.id,
        replacement("future", "2026-10-28", 900, 9),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert (
        manual.write_value(tmp_db, measure.id, request, principal=OWNER_PRINCIPAL, now=NOW).receipt
        == accepted.receipt
    )
    next_request = ManualIncrementBody(
        request_id="latest-next",
        date="2026-10-27",
        expected_definition_revision=1,
        basis="latest",
        operation="increment",
        delta=0.25,
    )
    assert (
        manual.write_value(
            tmp_db, measure.id, next_request, principal=OWNER_PRINCIPAL, now=NOW
        ).receipt.value
        == 1.75
    )
    assert [
        (row.date, row.value)
        for row in data.read_observations(tmp_db, measure.id, "2026-10-26", "2026-10-28")
    ] == [("2026-10-26", 2), ("2026-10-27", 1.75), ("2026-10-28", 900)]
    # A cumulative draft based on yesterday is stale after any accepted new date.
    with pytest.raises(PlannerError, match="value changed"):
        manual.write_value(
            tmp_db,
            measure.id,
            {**replacement("stale-latest", "2026-10-29", 3, 8), "basis": "latest"},
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )


def test_manual_authority_validation_and_http_state_contract(tmp_db: Connection) -> None:
    measure = manual_measure(tmp_db, zone="America/Los_Angeles")
    now = int(datetime(2026, 10, 28, 0, 30, tzinfo=UTC).timestamp())
    request = replacement("owner", "2026-10-27", 0, 0)
    for actor in (
        Principal(PrincipalKind.goal, "goal-fixture"),
        Principal(PrincipalKind.ticket, "ticket-fixture"),
    ):
        with pytest.raises(PlannerError):
            manual.write_value(tmp_db, measure.id, request, principal=actor, now=now)
    external = taken(tmp_db, source(tmp_db))
    with pytest.raises(PlannerError, match="manual measure"):
        manual.write_value(tmp_db, external.id, request, principal=OWNER_PRINCIPAL, now=now)
    for invalid_number in (True, None, "", float("nan"), float("inf")):
        with pytest.raises(PlannerError, match="finite number"):
            manual.write_value(
                tmp_db,
                measure.id,
                {**request, "value": invalid_number},  # type: ignore[typeddict-item]
                principal=OWNER_PRINCIPAL,
                now=now,
            )
    app = _app(tmp_db)
    app.state.clock = FakeClock(datetime.fromtimestamp(now, UTC))
    with TestClient(app) as client:
        initial_key = f"/api/measures/{measure.id}/manual-state"
        initial = client.get(initial_key)
        assert initial.status_code == 200, initial.text
        assert initial.json()["date"] == initial.json()["current_date"] == "2026-10-27"
        initial_cached = client.get("/api/resource-body", params={"key": initial_key}).json()
        assert initial_cached["body"] == initial.json()
        key = f"/api/measures/{measure.id}/manual-state?date=2026-10-27"
        before = client.get(key)
        assert before.status_code == 200 and before.json()["current_date"] == "2026-10-27"
        cached = client.get("/api/resource-body", params={"key": key}).json()
        assert cached["body"] == before.json()
        accepted = client.post(f"/api/measures/{measure.id}/manual-writes", json=request)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["receipt"]["actor"] == {"kind": "owner", "id": "owner"}
        stale = client.post(
            f"/api/measures/{measure.id}/manual-writes",
            json=replacement("stale-http", "2026-10-27", 1, 0),
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["detail"]["state"]["observation"]["value"] == 0
        after = client.get("/api/resource-body", params={"key": key}).json()
        assert after["revision"] != cached["revision"]
        assert after["body"] == json.loads(
            json.dumps(asdict(manual.read_state(tmp_db, measure.id, "2026-10-27", now=now)))
        )
        app.state.clock.set(datetime(2026, 10, 28, 8, tzinfo=UTC))
        tomorrow = client.get("/api/resource-body", params={"key": key}).json()
        assert tomorrow["revision"] != after["revision"]
        assert tomorrow["body"]["current_date"] == "2026-10-28"
        assert tomorrow["body"]["date"] == "2026-10-27"
    assert tmp_db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_goal_writes_require_its_current_saved_manual_binding(tmp_db: Connection) -> None:
    """Shared bindings grant writes, but another goal or an old receipt grants no capability."""
    from tests.unit.test_goal_pages import create

    from planner.goals import actions as goals

    shared = manual_measure(tmp_db)
    private = manual_measure(tmp_db)
    external = taken(tmp_db, source(tmp_db))
    derived = actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Derived",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula=ReferenceExpression(op="reference", measure_id=shared.id, day_offset=0),
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    chart = {
        "type": "chart",
        "id": "shared",
        "kind": "line",
        "placement": "primary",
        "measure_id": shared.id,
    }
    goal = create(
        tmp_db,
        kind="quantitative",
        primary_chart=chart,
        page={
            "kind": "headroom.page",
            "version": 1,
            "title": "Inputs",
            "summary": "",
            "blocks": [
                {
                    "type": "chart",
                    "id": item.id,
                    "kind": "line",
                    "placement": "supporting",
                    "measure_id": item.id,
                }
                for item in (external, derived)
            ],
        },
    )
    also_shared = create(tmp_db, kind="quantitative", primary_chart=chart)
    unrelated = create(
        tmp_db, kind="quantitative", primary_chart={**chart, "measure_id": private.id}
    )
    actor = Principal(PrincipalKind.goal, goal.id)
    first = replacement("goal-first", "2026-10-27", 4, 0)
    accepted = manual.write_value(tmp_db, shared.id, first, principal=actor, now=NOW)
    assert accepted.receipt.actor == actor and accepted.receipt.value == 4
    replay = manual.write_value(tmp_db, shared.id, first, principal=actor, now=NOW)
    assert replay.replayed and replay.receipt == accepted.receipt
    second_actor = Principal(PrincipalKind.goal, also_shared.id)
    incremented = manual.write_value(
        tmp_db,
        shared.id,
        ManualIncrementBody(
            request_id="goal-shared",
            date="2026-10-27",
            expected_definition_revision=1,
            basis="date",
            operation="increment",
            delta=2,
        ),
        principal=second_actor,
        now=NOW,
    )
    assert incremented.receipt.actor == second_actor and incremented.receipt.value == 6
    # These are real saved goals. Each denied measure is either foreign or nonmanual.
    for identifier, principal in (
        (shared.id, Principal(PrincipalKind.goal, unrelated.id)),
        (private.id, actor),
        (external.id, actor),
        (derived.id, actor),
    ):
        with pytest.raises(PlannerError, match="bound manual measures"):
            manual.write_value(
                tmp_db,
                identifier,
                replacement("goal-denied", "2026-10-27", 99, 0),
                principal=principal,
                now=NOW,
            )
    # Removing the saved binding revokes replay as well as new writes.
    goals.update_goal(
        tmp_db,
        goal.id,
        {
            "expected_revision": 1,
            "kind": "binary",
            "primary_checklist_block_id": None,
            "primary_chart": None,
        },
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    with pytest.raises(PlannerError, match="bound manual measures"):
        manual.write_value(tmp_db, shared.id, first, principal=actor, now=NOW)
    assert manual.read_state(tmp_db, shared.id, "2026-10-27", now=NOW).write_revision == 2
    assert tmp_db.execute("SELECT COUNT(*) FROM manual_measure_writes").fetchone()[0] == 2
