"""Retained-input arithmetic, descriptive metadata, graph and work-budget risks."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from planner.core.contracts import PlannerError
from planner.measures.contracts import (
    BinaryExpression,
    ConstantExpression,
    CountPeriod,
    Measure,
    MeasureDate,
    MeasureKind,
    MeasureOrigin,
    MeasureReadStatus,
    Observation,
    ReferenceExpression,
    SumDailyExpression,
)
from planner.measures.logic.evaluation import demanded_pairs, evaluate
from planner.measures.logic.formulas import parse_formula, validate_graph

NOW = int(datetime(2026, 10, 27, 12, tzinfo=UTC).timestamp())


def definition(
    identifier: str, *, kind: MeasureKind = MeasureKind.reading, period: CountPeriod | None = None
) -> Measure:
    return Measure(
        identifier,
        "project_vylo",
        identifier,
        "items",
        MeasureOrigin.taken,
        kind,
        "Europe/Berlin",
        period,
        "source",
        None,
        1,
        NOW,
        NOW,
    )


def test_exact_arithmetic_correction_missing_and_zero() -> None:
    taken = definition("taken")
    current = ReferenceExpression(op="reference", measure_id="taken", day_offset=0)
    previous = ReferenceExpression(op="reference", measure_id="taken", day_offset=-7)
    formula = BinaryExpression(
        op="multiply",
        left=BinaryExpression(
            op="divide",
            left=BinaryExpression(op="subtract", left=current, right=previous),
            right=previous,
        ),
        right=ConstantExpression(op="constant", value=100),
    )
    derived = replace(
        taken, id="growth", origin=MeasureOrigin.assembled, source_id=None, formula=formula
    )
    definitions = {"taken": taken, "growth": derived}
    pair = MeasureDate("growth", "2026-10-27")
    observations = {
        MeasureDate("taken", day): Observation("taken", day, value, "run", NOW, 1, None)
        for day, value in (("2026-10-20", 100), ("2026-10-27", 120))
    }
    assert evaluate((pair,), definitions, observations, now=NOW)[0].value == 20
    observations[MeasureDate("taken", "2026-10-27")] = replace(
        observations[MeasureDate("taken", "2026-10-27")], value=130
    )
    assert evaluate((pair,), definitions, observations, now=NOW)[0].value == 30
    observations[MeasureDate("taken", "2026-10-20")] = replace(
        observations[MeasureDate("taken", "2026-10-20")], value=0
    )
    assert (
        evaluate((pair,), definitions, observations, now=NOW)[0].status
        is MeasureReadStatus.division_by_zero
    )
    del observations[MeasureDate("taken", "2026-10-20")]
    missing = evaluate((pair,), definitions, observations, now=NOW)[0]
    assert missing.status is MeasureReadStatus.missing_input
    assert missing.missing_inputs == (MeasureDate("taken", "2026-10-20"),)


def test_explicit_window_arithmetic_ignores_descriptive_kinds_and_units() -> None:
    daily = definition("day", kind=MeasureKind.running_total)
    weekly = replace(
        daily,
        id="week",
        origin=MeasureOrigin.assembled,
        source_id=None,
        kind=MeasureKind.period_count,
        unit="different label",
        timezone="UTC",
        formula=SumDailyExpression(op="sum_daily", measure_id="day", period=CountPeriod.week),
    )
    definitions = {"day": daily, "week": weekly}
    validate_graph("week", definitions)
    # Tuesday selects its ISO week; an unfinished period is not an application error.
    pair = MeasureDate("week", "2026-10-27")
    dates = (
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-10-31",
        "2026-11-01",
    )
    observations = {
        MeasureDate("day", day): Observation("day", day, value, "run", NOW, 1, None)
        for day, value in zip(dates, (1, 2, 0, -4.5, 5, 6, 7), strict=True)
    }
    assert evaluate((pair,), definitions, observations, now=NOW)[0].value == 16.5
    assert (
        evaluate((MeasureDate("week", "2026-11-01"),), definitions, observations, now=NOW)[0].value
        == 16.5
    )
    del observations[MeasureDate("day", "2026-10-28")]
    assert (
        evaluate((pair,), definitions, observations, now=NOW)[0].status
        is MeasureReadStatus.missing_input
    )


def test_graph_integrity_without_numeric_kind_policing() -> None:
    first = replace(
        definition("a"),
        source_id=None,
        origin=MeasureOrigin.assembled,
        formula=ReferenceExpression(op="reference", measure_id="b", day_offset=-1),
    )
    second = replace(
        first, id="b", formula=ReferenceExpression(op="reference", measure_id="a", day_offset=-1)
    )
    with pytest.raises(PlannerError, match="cycle"):
        validate_graph("a", {"a": first, "b": second})
    count = replace(
        first,
        kind=MeasureKind.period_count,
        period=CountPeriod.week,
        formula=ReferenceExpression(op="reference", measure_id="b", day_offset=0),
    )
    validate_graph("a", {"a": count, "b": definition("b")})
    total = replace(
        first,
        kind=MeasureKind.running_total,
        formula=BinaryExpression(
            op="subtract",
            left=ReferenceExpression(op="reference", measure_id="b", day_offset=0),
            right=ConstantExpression(op="constant", value=1),
        ),
    )
    validate_graph("a", {"a": total, "b": definition("b", kind=MeasureKind.running_total)})
    assert (
        parse_formula({"op": "reference", "measure_id": "b", "day_offset": 1000})["op"]
        == "reference"
    )
    with pytest.raises(PlannerError, match="finite"):
        parse_formula({"op": "constant", "value": True})


def test_total_range_independence_and_interpreter_deep_chain() -> None:
    taken = definition("total", kind=MeasureKind.running_total)
    total = replace(
        taken,
        id="combined",
        source_id=None,
        origin=MeasureOrigin.assembled,
        formula=BinaryExpression(
            op="multiply",
            left=ReferenceExpression(op="reference", measure_id="total", day_offset=0),
            right=ConstantExpression(op="constant", value=2),
        ),
    )
    definitions = {"total": taken, "combined": total}
    validate_graph("combined", definitions)
    observations = {
        MeasureDate("total", day): Observation("total", day, number, "run", NOW, 1, None)
        for day, number in (("2026-10-25", 10), ("2026-10-26", 13))
    }
    one = MeasureDate("combined", "2026-10-26")
    assert (
        evaluate((one,), definitions, observations, now=NOW)[0]
        == evaluate(
            (MeasureDate("combined", "2026-10-25"), one), definitions, observations, now=NOW
        )[1]
    )
    # Combined valid expressions can exceed Python's stack without causing an unhandled error.
    chain = {"0": definition("0")}
    for index in range(1, 64):
        formula = parse_formula({"op": "reference", "measure_id": str(index - 1), "day_offset": 0})
        for _ in range(15):
            formula = BinaryExpression(
                op="add", left=formula, right=ConstantExpression(op="constant", value=0)
            )
        chain[str(index)] = replace(
            definition(str(index)), origin=MeasureOrigin.assembled, source_id=None, formula=formula
        )
    validate_graph("63", chain)
    roots = (MeasureDate("63", "2026-10-27"),)
    assert len(demanded_pairs(roots, chain, now=NOW)) == 64
    result = evaluate(roots, chain, {}, now=NOW)[0]
    assert result.status in (MeasureReadStatus.arithmetic_limit, MeasureReadStatus.missing_input)
