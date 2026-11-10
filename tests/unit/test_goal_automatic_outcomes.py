"""Prove saved achievements follow evidence without a goal page or GET request."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from sqlite3 import Connection

import pytest
from tests.unit.test_manual_measures import manual_measure, replacement
from tests.unit.test_measure_persistence import NOW, collect, run, source, taken

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, PlannerError
from planner.goals import actions, configuration, data, scheduling
from planner.goals.contracts import Goal, GoalCompletionRule
from planner.goals.outcomes import reconcile_completions
from planner.goals.rule_evaluation import evaluate_completion_date, parse_completion_rule
from planner.goals.scheduling import GoalCompletionParticipant
from planner.measure_sources.schedule_loop import SourceScheduleLoop
from planner.measures import actions as measure_actions
from planner.measures import manual
from planner.measures.contracts import (
    CandidateObservation,
    CreateMeasureBody,
    FormulaExpression,
    MeasureKind,
    MeasureOrigin,
    UpdateMeasureBody,
)
from planner.measures.data import transaction


def finish_line(measure_id: str, *, first_date: str = "2026-10-25") -> GoalCompletionRule:
    return {
        "actual": {"op": "reference", "measure_id": measure_id, "day_offset": 0},
        "target": {"op": "constant", "value": 10},
        "direction": "atleast",
        "first_date": first_date,
    }


def numeric_goal(conn: Connection, rule: GoalCompletionRule) -> Goal:
    return actions.create_goal(
        conn,
        {
            "project_id": "project_vylo",
            "title": "Reach the finish line",
            "kind": "quantitative",
            "active": False,
            "deadline": "2026-10-26",
            # A different visual target cannot silently replace the finish line.
            "primary_chart": {"type": "chart", "kind": "number", "target": 1000},
            "completion_rule": rule,
        },
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )


def write(conn: Connection, measure_id: str, day: str, value: float, request: str) -> None:
    revision = manual.read_state(conn, measure_id, day, now=NOW).write_revision
    manual.write_value(
        conn,
        measure_id,
        replacement(request, day, value, revision),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )


def test_background_clock_persists_and_corrects_an_inactive_achievement_without_get(
    tmp_db: Connection,
) -> None:
    measure = manual_measure(tmp_db)
    goal = numeric_goal(tmp_db, finish_line(measure.id))
    clock = FakeClock(datetime.fromtimestamp(NOW, UTC))
    participant = GoalCompletionParticipant()
    loop = SourceScheduleLoop(
        str(tmp_db.execute("PRAGMA database_list").fetchone()[2]),
        clock,
        participants=(participant,),
    )
    write(tmp_db, measure.id, "2026-10-25", 8, "before")
    write(tmp_db, measure.id, "2026-10-26", 10, "hit")
    loop.poll_once()
    accepted = data.read_goal(tmp_db, goal.id)
    assert accepted.completion_date == "2026-10-26"
    assert not accepted.active and not accepted.binary_checked
    assert accepted.revision == goal.revision + 1
    loop.poll_once()
    assert data.read_goal(tmp_db, goal.id) == accepted

    write(tmp_db, measure.id, "2026-10-27", 4, "fluctuation")
    loop.poll_once()
    assert data.read_goal(tmp_db, goal.id) == accepted
    write(tmp_db, measure.id, "2026-10-26", 7, "correction")
    loop.poll_once()
    assert data.read_goal(tmp_db, goal.id).completion_date is None
    write(tmp_db, measure.id, "2026-10-25", 12, "earlier-correction")
    loop.poll_once()
    assert data.read_goal(tmp_db, goal.id).completion_date == "2026-10-25"

    saved = data.read_goal(tmp_db, goal.id)
    rule = finish_line(measure.id, first_date="2026-10-27")
    saved = actions.update_goal(
        tmp_db,
        goal.id,
        {"completion_rule": rule},
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    assert saved.completion_date is None
    write(tmp_db, measure.id, "2026-10-28", 20, "future")
    loop.poll_once()
    assert data.read_goal(tmp_db, goal.id).completion_date is None
    clock.set(datetime(2026, 10, 28, 12, tzinfo=UTC))
    loop.poll_once()
    assert data.read_goal(tmp_db, goal.id).completion_date == "2026-10-28"


def test_collected_formula_evidence_uses_the_finish_line_and_first_date(tmp_db: Connection) -> None:
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier)
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(measure.id, "2026-10-25", 5),
            CandidateObservation(measure.id, "2026-10-26", 6),
        ),
    )
    actual: FormulaExpression = {
        "op": "multiply",
        "left": {"op": "reference", "measure_id": measure.id, "day_offset": 0},
        "right": {"op": "constant", "value": 2},
    }
    rule = finish_line(measure.id)
    rule["actual"] = actual
    rule["target"] = {
        "op": "add",
        "left": {"op": "constant", "value": 10},
        "right": {"op": "elapsed_days", "start": "2026-10-25"},
    }
    rule = parse_completion_rule(tmp_db, rule)
    assert evaluate_completion_date(tmp_db, rule, now=NOW) == "2026-10-25"
    rule["first_date"] = "2026-10-26"
    assert evaluate_completion_date(tmp_db, rule, now=NOW) == "2026-10-26"
    rule["direction"] = "atmost"
    assert evaluate_completion_date(tmp_db, rule, now=NOW) is None

    # A definition edit changes a bound formula without any goal edit or GET.
    # The participant's automatic-measures stamp must invalidate its old result.
    assembled = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Twice the collected value",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula=actual,
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    goal = numeric_goal(tmp_db, finish_line(assembled.id))
    assert goal.completion_date == "2026-10-25"
    participant = GoalCompletionParticipant()
    current = datetime.fromtimestamp(NOW, UTC)
    participant.poll(tmp_db, local_now=current, now=NOW)
    measure_actions.update_measure(
        tmp_db,
        assembled.id,
        UpdateMeasureBody(
            expected_revision=assembled.definition_revision,
            formula={"op": "reference", "measure_id": measure.id, "day_offset": 0},
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    participant.poll(tmp_db, local_now=current, now=NOW)
    corrected = data.read_goal(tmp_db, goal.id)
    assert corrected.completion_date is None
    assert corrected.completion_rule == goal.completion_rule
    assert corrected.revision == goal.revision + 1


def test_constants_and_future_references_cannot_fabricate_past_achievement(
    tmp_db: Connection,
) -> None:
    constant = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="A constant is not evidence",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula={"op": "constant", "value": 100},
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    rule = finish_line(constant.id)
    assert evaluate_completion_date(tmp_db, rule, now=NOW) is None
    rule["actual"] = {"op": "constant", "value": 100}
    with pytest.raises(PlannerError, match="retained evidence"):
        parse_completion_rule(tmp_db, rule)

    measure = manual_measure(tmp_db)
    write(tmp_db, measure.id, "2026-10-28", 20, "dated-future")
    rule = finish_line(measure.id)
    rule["actual"] = {"op": "reference_at", "measure_id": measure.id, "date": "2026-10-28"}
    assert evaluate_completion_date(tmp_db, rule, now=NOW) is None
    tomorrow = int(datetime(2026, 10, 28, 12, tzinfo=UTC).timestamp())
    assert evaluate_completion_date(tmp_db, rule, now=tomorrow) == "2026-10-28"


def test_exhausted_evaluation_preserves_saved_achievement(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    measure = manual_measure(tmp_db)
    write(tmp_db, measure.id, "2026-10-26", 10, "budget-hit")
    goal = numeric_goal(tmp_db, finish_line(measure.id))
    assert goal.completion_date == "2026-10-26"
    assert goal.completion_rule is not None
    monkeypatch.setattr(configuration, "MAX_PROJECTED_CHART_DATES", 2)
    with pytest.raises(PlannerError, match="date budget"):
        evaluate_completion_date(tmp_db, goal.completion_rule, now=NOW)
    assert reconcile_completions(tmp_db, now=NOW) == 0
    assert data.read_goal(tmp_db, goal.id) == goal
    assert "goal completion evaluation failed" in caplog.text
    stored = tmp_db.execute(
        "SELECT completion_rule FROM goals WHERE id=?", (goal.id,)
    ).fetchone()[0]
    assert json.loads(stored) == goal.completion_rule


def test_scheduler_cache_does_not_swallow_a_commit_after_its_snapshot(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    measure = manual_measure(tmp_db)
    goal = numeric_goal(tmp_db, finish_line(measure.id))
    participant = GoalCompletionParticipant()
    calls = 0

    @contextmanager
    def commit_after_snapshot(conn: Connection) -> Iterator[None]:
        nonlocal calls
        with transaction(conn):
            yield
        calls += 1
        if calls == 1:
            # This simulates a second writer immediately after the reconciler's
            # snapshot commits, before it assigns its in-memory revision memo.
            write(conn, measure.id, "2026-10-26", 10, "after-snapshot")

    monkeypatch.setattr(scheduling, "transaction", commit_after_snapshot)
    current = datetime.fromtimestamp(NOW, UTC)
    participant.poll(tmp_db, local_now=current, now=NOW)
    assert data.read_goal(tmp_db, goal.id).completion_date is None
    participant.poll(tmp_db, local_now=current, now=NOW)
    assert data.read_goal(tmp_db, goal.id).completion_date == "2026-10-26"
    participant.poll(tmp_db, local_now=current, now=NOW)
    assert calls == 2  # An unchanged snapshot does not scan again.
