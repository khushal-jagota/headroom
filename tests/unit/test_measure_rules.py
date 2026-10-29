"""Retained-input arithmetic, descriptive metadata, graph and work-budget risks."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from planner.core.contracts import PlannerError
from planner.measures.contracts import (
    BinaryExpression,
    ConstantExpression,
    CountPeriod,
    ElapsedDaysExpression,
    FormulaExpression,
    IfBeforeExpression,
    Measure,
    MeasureDate,
    MeasureKind,
    MeasureOrigin,
    MeasureReadStatus,
    Observation,
    ReferenceAtExpression,
    ReferenceExpression,
    SumDailyExpression,
)
from planner.measures.logic.evaluation import (
    Evaluation,
    demanded_pairs,
    evaluate,
    expression_references,
    phase_changed,
)
from planner.measures.logic.formulas import dependencies, parse_formula, validate_graph

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


def test_dated_paths_match_approved_examples_in_both_entry_points() -> None:
    # Independent dated expressions and assembled measures must share arithmetic.
    elapsed = ElapsedDaysExpression(op="elapsed_days", start="2026-10-01")

    def constant(value: float) -> ConstantExpression:
        return ConstantExpression(op="constant", value=value)

    straight = BinaryExpression(
        op="add",
        left=constant(100),
        right=BinaryExpression(
            op="divide",
            left=BinaryExpression(op="multiply", left=constant(60), right=elapsed),
            right=constant(30),
        ),
    )
    compound = BinaryExpression(
        op="multiply",
        left=constant(100),
        right=BinaryExpression(
            op="power",
            left=constant(1.1),
            right=BinaryExpression(op="divide", left=elapsed, right=constant(7)),
        ),
    )
    piecewise = IfBeforeExpression(
        op="if_before",
        date="2026-10-15",
        before=straight,
        otherwise=IfBeforeExpression(
            op="if_before",
            date="2026-10-22",
            before=constant(128),
            otherwise=BinaryExpression(
                op="multiply",
                left=constant(128),
                right=BinaryExpression(
                    op="power",
                    left=constant(1.1),
                    right=BinaryExpression(
                        op="divide",
                        left=BinaryExpression(op="subtract", left=elapsed, right=constant(21)),
                        right=constant(7),
                    ),
                ),
            ),
        ),
    )
    formulas: tuple[FormulaExpression, ...] = (
        straight,
        compound,
        piecewise,
        BinaryExpression(op="max", left=constant(130), right=straight),
        BinaryExpression(op="min", left=constant(150), right=straight),
    )
    expected = (
        ("2026-10-01", (100, 100, 100, 130, 100)),
        ("2026-10-08", (114, 110, 114, 130, 114)),
        ("2026-10-15", (128, 121, 128, 130, 128)),
        ("2026-10-22", (142, 133.1, 128, 142, 142)),
        ("2026-10-29", (156, 146.41, 140.8, 156, 150)),
        ("2026-10-31", (160, 150.451743, 144.686875, 160, 150)),
    )
    for index, formula in enumerate(formulas):
        measure = replace(
            definition("path"),
            source_id=None,
            origin=MeasureOrigin.assembled,
            formula=parse_formula(formula),
        )
        context = Evaluation({measure.id: measure}, {}, now=NOW)
        for day, values in expected:
            expression = context.expression(formula, day, "Europe/Berlin")
            assert expression.value == pytest.approx(values[index])
            assert context.measure(MeasureDate(measure.id, day)).value == expression.value


def test_date_branches_are_static_dependencies_but_lazy_demands_and_values() -> None:
    absent = ReferenceAtExpression(op="reference_at", measure_id="deleted", date="2026-10-01")
    divide_by_zero = BinaryExpression(
        op="divide",
        left=ConstantExpression(op="constant", value=1),
        right=ConstantExpression(op="constant", value=0),
    )
    formula = IfBeforeExpression(
        op="if_before",
        date="2026-10-15",
        before=ConstantExpression(op="constant", value=100),
        otherwise=IfBeforeExpression(
            op="if_before", date="2026-10-22", before=absent, otherwise=divide_by_zero
        ),
    )
    assert dependencies(formula) == {"deleted"}
    assembled = replace(
        definition("path"), source_id=None, origin=MeasureOrigin.assembled, formula=formula
    )
    with pytest.raises(PlannerError, match="input measure"):
        validate_graph(assembled.id, {assembled.id: assembled})
    context = Evaluation({assembled.id: assembled}, {}, now=NOW)
    before = context.expression(formula, "2026-10-14", "Europe/Berlin")
    assert before.value == 100 and before.inputs == () and before.missing_inputs == ()
    assert expression_references(formula, "2026-10-14", "UTC", NOW) == ()
    missing = context.expression(formula, "2026-10-15", "UTC")
    assert missing.status is MeasureReadStatus.missing_input
    assert missing.missing_inputs == (MeasureDate("deleted", "2026-10-01"),)
    assert missing.inputs == ()
    root = MeasureDate("path", "2026-10-15")
    assert demanded_pairs((root,), context.definitions, now=NOW, missing_ok=True) == {
        root,
        MeasureDate("deleted", "2026-10-01"),
    }
    assert context.expression(formula, "2026-10-22", "UTC").status is (
        MeasureReadStatus.division_by_zero
    )
    # An inactive nested boundary never splits the currently selected path.
    dormant = IfBeforeExpression(
        op="if_before",
        date="2026-11-01",
        before=ConstantExpression(op="constant", value=0),
        otherwise=formula,
    )
    assert not phase_changed(dormant, "2026-10-15")
    assert phase_changed(dormant, "2026-11-01")
    assert phase_changed(formula, "2026-10-15")
    assert phase_changed(formula, "2026-10-22")
    assert not phase_changed(formula, "2026-10-23")
    assert not phase_changed(formula, "0001-01-01")


def test_elapsed_days_use_signed_calendar_dates_across_dst_and_strict_iso_dates() -> None:
    context = Evaluation({}, {}, now=NOW)
    for first, last in (("2026-03-28", "2026-03-30"), ("2026-10-24", "2026-10-26")):
        formula = ElapsedDaysExpression(op="elapsed_days", start=first)
        assert context.expression(formula, last, "Europe/Berlin").value == 2
        assert context.expression(formula, last, "Pacific/Auckland").value == 2
        reverse = ElapsedDaysExpression(op="elapsed_days", start=last)
        assert context.expression(reverse, first, "Europe/Berlin").value == -2
    for invalid_date in ("20261001", "2026-2-01", "2026-02-29", "2026-10-01T00:00:00", None):
        for raw in (
            {"op": "elapsed_days", "start": invalid_date},
            {"op": "reference_at", "measure_id": "taken", "date": invalid_date},
            {
                "op": "if_before",
                "date": invalid_date,
                "before": {"op": "constant", "value": 1},
                "otherwise": {"op": "constant", "value": 2},
            },
        ):
            with pytest.raises(PlannerError, match="date"):
                parse_formula(raw)
    for base, exponent in ((-1, 0.5), (0, -1), (1e308, 2)):
        power = BinaryExpression(
            op="power",
            left=ConstantExpression(op="constant", value=base),
            right=ConstantExpression(op="constant", value=exponent),
        )
        assert context.expression(power, "2026-10-29", "UTC").status is (
            MeasureReadStatus.invalid_value
        )


def test_fixed_references_reuse_retained_provenance_and_follow_corrections() -> None:
    measure = replace(definition("baseline"), timezone="Pacific/Auckland", definition_revision=2)
    pair = MeasureDate(measure.id, "2026-10-01")
    formula = ReferenceAtExpression(op="reference_at", measure_id=measure.id, date=pair.date)
    observations = {pair: Observation(measure.id, pair.date, 100, None, NOW, 1, None, "write-1")}
    context = Evaluation({measure.id: measure}, observations, now=NOW)
    for day in ("2026-09-01", "2026-10-29", "2027-01-01"):
        result = context.expression(formula, day, "Europe/Berlin")
        assert result.value == 100
        assert result.inputs[0].date == pair.date
        assert result.inputs[0].definition_revision == 2
        assert result.inputs[0].manual_write_id == "write-1"
        assert expression_references(formula, day, "Europe/Berlin", NOW) == (pair,)
    corrected = {pair: replace(observations[pair], value=120, manual_write_id="write-2")}
    result = Evaluation({measure.id: measure}, corrected, now=NOW).expression(
        formula, "2026-10-29", "Europe/Berlin"
    )
    assert result.value == 120 and result.inputs[0].manual_write_id == "write-2"


def test_demand_limit_counts_unique_transitive_pairs_without_capping_ordinary_reads() -> None:
    source = definition("daily")
    assembled = replace(
        definition("weekly"),
        source_id=None,
        origin=MeasureOrigin.assembled,
        formula=SumDailyExpression(op="sum_daily", measure_id=source.id, period=CountPeriod.week),
    )
    definitions = {source.id: source, assembled.id: assembled}
    roots = (MeasureDate(assembled.id, "2026-10-27"),)
    assert len(demanded_pairs(roots + roots, definitions, now=NOW, limit=8)) == 8
    with pytest.raises(PlannerError, match="7 pairs.*narrower range"):
        demanded_pairs(roots, definitions, now=NOW, limit=7)
    assert len(demanded_pairs(roots, definitions, now=NOW)) == 8


def test_phase_boundaries_follow_composed_references_at_their_evaluation_dates() -> None:
    phase = replace(
        definition("phase"),
        source_id=None,
        origin=MeasureOrigin.assembled,
        formula=IfBeforeExpression(
            op="if_before",
            date="2026-10-28",
            before=ConstantExpression(op="constant", value=100),
            otherwise=ConstantExpression(op="constant", value=120),
        ),
    )
    nested = replace(
        phase,
        id="nested",
        formula=ReferenceExpression(op="reference", measure_id=phase.id, day_offset=0),
    )
    definitions = {phase.id: phase, nested.id: nested}
    context = Evaluation(definitions, {}, now=NOW)
    reference = ReferenceExpression(op="reference", measure_id=nested.id, day_offset=0)
    assert context.expression(reference, "2026-10-27", "UTC").value == 100
    assert context.expression(reference, "2026-10-28", "UTC").value == 120
    assert phase_changed(reference, "2026-10-28", definitions)
    assert not phase_changed(reference, "2026-10-29", definitions)
    shifted = ReferenceExpression(op="reference", measure_id=phase.id, day_offset=-2)
    assert not phase_changed(shifted, "2026-10-28", definitions)
    assert phase_changed(shifted, "2026-10-30", definitions)
    fixed = ReferenceAtExpression(op="reference_at", measure_id=phase.id, date="2026-10-28")
    assert not phase_changed(fixed, "2026-10-28", definitions)
    lazy = IfBeforeExpression(
        op="if_before",
        date="2026-11-01",
        before=ConstantExpression(op="constant", value=0),
        otherwise=reference,
    )
    assert not phase_changed(lazy, "2026-10-28", definitions)
    assert phase_changed(lazy, "2026-11-01", definitions)
    # Separate paths cannot cancel when another reference already uses the new phase.
    combined = BinaryExpression(op="add", left=fixed, right=reference)
    assert phase_changed(combined, "2026-10-28", definitions)
    monthly = SumDailyExpression(op="sum_daily", measure_id=phase.id, period=CountPeriod.month)
    assert phase_changed(monthly, "2026-11-01", definitions)
    # November and December differ in length, but every contribution uses the same phase.
    assert not phase_changed(monthly, "2026-12-01", definitions)
    missing = ReferenceExpression(op="reference", measure_id="absent", day_offset=0)
    assert not phase_changed(missing, "2026-10-28", definitions)
    out_of_range = ReferenceExpression(op="reference", measure_id=phase.id, day_offset=999999999)
    assert not phase_changed(out_of_range, "2026-10-28", definitions)
